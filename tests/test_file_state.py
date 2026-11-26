"""Read-before-edit and staleness checks for edit/write (tools/file_state.py)."""

import os
from pathlib import Path

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.edit import edit_file
from crabagent.core.agent.tools.file_state import FileStateTracker, session_tracker
from crabagent.core.agent.tools.read import read_file
from crabagent.core.agent.tools.write import write_file


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_edit_refuses_a_file_never_read(ws):
    ctx = AgentContext(workspace=ws)
    result = edit_file("a.py", "x = 1", "x = 2", context=ctx)
    assert result.startswith("Error: read a.py with the read tool")
    assert (ws / "a.py").read_text() == "x = 1\n"


def test_edit_after_read_succeeds_and_again_without_rereading(ws):
    ctx = AgentContext(workspace=ws)
    read_file("a.py", context=ctx)
    assert "Successfully" in edit_file("a.py", "x = 1", "x = 2", context=ctx)
    # Its own edit keeps the stamp current: a second edit needs no new read.
    assert "Successfully" in edit_file("a.py", "x = 2", "x = 3", context=ctx)
    assert (ws / "a.py").read_text() == "x = 3\n"


def test_edit_refuses_after_someone_else_changed_the_file(ws):
    ctx = AgentContext(workspace=ws)
    read_file("a.py", context=ctx)
    (ws / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    result = edit_file("a.py", "x = 1", "x = 9", context=ctx)
    assert "changed on disk since you last read it" in result
    assert (ws / "a.py").read_text() == "x = 1\ny = 2\n"
    read_file("a.py", context=ctx)
    assert "Successfully" in edit_file("a.py", "x = 1", "x = 9", context=ctx)


def test_a_touch_without_a_content_change_is_not_stale(ws):
    ctx = AgentContext(workspace=ws)
    read_file("a.py", context=ctx)
    st = (ws / "a.py").stat()
    os.utime(ws / "a.py", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert "Successfully" in edit_file("a.py", "x = 1", "x = 2", context=ctx)


def test_parallel_agents_see_each_others_edits(ws):
    coder_a, coder_b = AgentContext(workspace=ws), AgentContext(workspace=ws)
    read_file("a.py", context=coder_a)
    read_file("a.py", context=coder_b)
    assert "Successfully" in edit_file("a.py", "x = 1", "x = 2", context=coder_a)
    assert "changed on disk" in edit_file("a.py", "x = 1", "x = 3", context=coder_b)


def test_write_refuses_to_overwrite_an_unread_file_but_creates_new_ones(ws):
    ctx = AgentContext(workspace=ws)
    assert "before overwriting it" in write_file("a.py", "new\n", context=ctx)
    assert (ws / "a.py").read_text() == "x = 1\n"
    assert "Successfully" in write_file("b.py", "b = 1\n", context=ctx)
    # A file it created itself can be edited straight away.
    assert "Successfully" in edit_file("b.py", "b = 1", "b = 2", context=ctx)


def test_write_refuses_a_stale_overwrite(ws):
    ctx = AgentContext(workspace=ws)
    read_file("a.py", context=ctx)
    (ws / "a.py").write_text("changed\n", encoding="utf-8")
    assert "changed on disk" in write_file("a.py", "mine\n", context=ctx)
    assert (ws / "a.py").read_text() == "changed\n"


def test_batch_read_counts_as_a_read(ws):
    (ws / "b.py").write_text("b = 1\n", encoding="utf-8")
    ctx = AgentContext(workspace=ws)
    out = read_file(files=[{"file_path": "a.py"}, {"file_path": "b.py", "limit": 1}], context=ctx)
    assert "[File: a.py" in out and "[File: b.py" in out
    assert "Successfully" in edit_file("b.py", "b = 1", "b = 2", context=ctx)


def test_batch_read_rejects_mixing_and_too_many_files(ws):
    assert read_file("a.py", files=[{"file_path": "a.py"}]).startswith("Error")
    assert read_file(files=[{"file_path": "a.py"}] * 11).startswith("Error: at most")
    assert read_file().startswith("Error: file_path is required")


def test_written_paths_lists_only_changed_files(ws):
    ctx = AgentContext(workspace=ws)
    read_file("a.py", context=ctx)
    write_file("c.py", "c = 1\n", context=ctx)
    assert ctx.file_state.written_paths() == [str((ws / "c.py").resolve())]


def test_no_context_means_no_checks(ws):
    assert "Successfully" in edit_file(str(ws / "a.py"), "x = 1", "x = 2")


def test_a_session_tracker_spans_turns():
    assert session_tracker("s-1") is session_tracker("s-1")
    assert session_tracker("s-1") is not session_tracker("s-2")
    assert isinstance(session_tracker("s-3"), FileStateTracker)

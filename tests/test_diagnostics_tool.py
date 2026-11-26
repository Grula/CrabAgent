"""Post-edit diagnostics and the diagnostics tool (tools/diagnostics.py)."""

import shutil
from pathlib import Path

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools import diagnostics as diag
from crabagent.core.agent.tools.edit import edit_file
from crabagent.core.agent.tools.read import read_file
from crabagent.core.agent.tools.write import write_file

needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is not installed")


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "m.py").write_text("import os\n\n\ndef f():\n    return 1\n", encoding="utf-8")
    return tmp_path


@needs_ruff
def test_edit_reports_only_problems_it_introduced(ws):
    ctx = AgentContext(workspace=ws)
    read_file("m.py", context=ctx)
    result = edit_file("m.py", "return 1", "return missing_name", context=ctx)
    assert "new problems from this change" in result
    assert "F821" in result and "missing_name" in result
    # The unused `os` import was there before the edit: not this edit's problem.
    assert "F401" not in result


@needs_ruff
def test_clean_edit_says_so(ws):
    ctx = AgentContext(workspace=ws)
    read_file("m.py", context=ctx)
    assert edit_file("m.py", "return 1", "return 2", context=ctx).endswith("Diagnostics: no new problems.")


def test_syntax_error_is_reported_without_any_checker(ws, monkeypatch):
    monkeypatch.setattr(diag.shutil, "which", lambda _name: None)
    ctx = AgentContext(workspace=ws)
    read_file("m.py", context=ctx)
    result = edit_file("m.py", "def f():", "def f(:", context=ctx)
    assert result.startswith("Successfully")
    assert "syntax error" in result and "Fix it before anything else" in result


def test_without_ruff_a_clean_python_write_says_what_was_checked(ws, monkeypatch):
    monkeypatch.setattr(diag.shutil, "which", lambda _name: None)
    result = write_file("new.py", "x = 1\n", context=AgentContext(workspace=ws))
    assert "syntax OK (install ruff" in result


def test_invalid_json_write_is_flagged_and_other_files_are_not_checked(ws):
    ctx = AgentContext(workspace=ws)
    assert "invalid JSON" in write_file("c.json", "{bad", context=ctx)
    assert write_file("ok.json", '{"a": 1}', context=ctx).endswith("bytes to ok.json")
    assert write_file("notes.md", "# hi\n", context=ctx).endswith("bytes to notes.md")


def test_new_problems_are_matched_by_code_and_message_not_line():
    before = [("F401", "`os` imported but unused", 1)]
    after = [("F401", "`os` imported but unused", 3), ("F821", "Undefined name `x`", 9)]
    assert diag._new_problems(before, after) == [("F821", "Undefined name `x`", 9)]


@pytest.mark.asyncio
async def test_tool_defaults_to_the_files_changed_in_this_run(ws, monkeypatch):
    seen: dict = {}

    async def fake_python(files, workspace):
        seen["files"] = files
        return ["m.py:5: [F821] Undefined name `y`"], []

    monkeypatch.setattr(diag, "_check_python", fake_python)
    ctx = AgentContext(workspace=ws)
    assert (await diag.diagnostics(context=ctx)).startswith("No files changed")

    read_file("m.py", context=ctx)
    edit_file("m.py", "return 1", "return y", context=ctx)
    result = await diag.diagnostics(context=ctx)
    assert [f.name for f in seen["files"]] == ["m.py"]
    assert result.startswith("Checked 1 file: 1 problem.") and "F821" in result


@pytest.mark.asyncio
async def test_tool_rejects_paths_that_are_not_files(ws):
    result = await diag.diagnostics(paths=["nope.py"], context=AgentContext(workspace=ws))
    assert result.startswith("Error: not a file")


@pytest.mark.asyncio
async def test_tool_lists_unsupported_files(ws):
    (ws / "notes.md").write_text("x\n", encoding="utf-8")
    result = await diag.diagnostics(paths=["notes.md"], context=AgentContext(workspace=ws))
    assert result.startswith("Checked 0 files: no problems.")
    assert "Not checked (no checker for this type): notes.md" in result


@needs_ruff
@pytest.mark.asyncio
async def test_tool_runs_ruff_with_the_project_rules(ws, monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(diag.shutil, "which", lambda name: None if "pyright" in name else real_which(name))
    result = await diag.diagnostics(paths=["m.py"], context=AgentContext(workspace=ws))
    assert "m.py:1: [F401]" in result
    assert "no type checker installed" in result


@needs_ruff
def test_a_failing_ruff_is_reported_not_read_as_clean(ws):
    # Review finding: an invalid ruff config made ruff exit 2 with no output,
    # which used to read as "no new problems".
    (ws / "pyproject.toml").write_text('[tool.ruff]\nline-length = "abc"\n', encoding="utf-8")
    ctx = AgentContext(workspace=ws)
    read_file("m.py", context=ctx)
    result = edit_file("m.py", "return 1", "return missing_name", context=ctx)
    assert "lint not checked (ruff failed" in result
    assert "no new problems" not in result


def test_editing_a_jsonc_file_is_not_flagged(ws):
    (ws / "tsconfig.json").write_text('{\n  // comment\n  "a": 1,\n}\n', encoding="utf-8")
    ctx = AgentContext(workspace=ws)
    read_file("tsconfig.json", context=ctx)
    assert "invalid JSON" not in edit_file("tsconfig.json", '"a": 1', '"a": 2', context=ctx)
    assert "invalid JSON" not in write_file("tsconfig.json", "{\n  // still jsonc\n}\n", context=ctx)
    (ws / "strict.json").write_text('{"a": 1}', encoding="utf-8")
    read_file("strict.json", context=ctx)
    assert "invalid JSON" in edit_file("strict.json", '"a": 1', '"a": 1,', context=ctx)


@pytest.mark.asyncio
async def test_tsc_is_never_taken_from_outside_the_workspace(tmp_path, monkeypatch):
    # Review finding: a .ts path outside the workspace made the tool run that
    # folder's node_modules/.bin/tsc.
    ws, elsewhere = tmp_path / "ws", tmp_path / "elsewhere"
    ws.mkdir()
    (elsewhere / "node_modules" / ".bin").mkdir(parents=True)
    (elsewhere / "tsconfig.json").write_text("{}", encoding="utf-8")
    marker = tmp_path / "PWNED"
    fake = elsewhere / "node_modules" / ".bin" / "tsc"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    fake.chmod(0o755)
    (elsewhere / "a.ts").write_text("export const a = 1;\n", encoding="utf-8")
    monkeypatch.setattr(diag.shutil, "which", lambda _name: None)

    result = await diag.diagnostics(paths=[str(elsewhere / "a.ts")], context=AgentContext(workspace=ws))

    assert not marker.exists()
    assert "no tsconfig.json above it inside the workspace" in result


def test_diagnostics_asks_first_unless_a_profile_allows_it():
    from crabagent.core.agent.tools.registry import registry
    from crabagent.core.database import DEFAULT_AGENTS

    assert registry.get("diagnostics").requires_permission
    perms = {a["name"]: a["tool_permissions"].get("diagnostics") for a in DEFAULT_AGENTS}
    assert perms["coder"] == perms["tester"] == perms["the_reviewer"] == "auto"
    assert perms["architect"] == perms["scout"] == "deny"

"""The language-server client's failure paths (code_index/lsp.py).

No Python language server is needed here: ``server_command`` is replaced by a
plain child process that answers nothing (a hung or broken install), so these
tests run everywhere, unlike tests/test_code_nav.py which needs basedpyright.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from crabagent.core.code_index import lsp


def _silent_server() -> list[str]:
    """A child that stays alive and answers nothing, like a server stuck in initialize."""
    return [sys.executable, "-c", "import time; time.sleep(30)"]


def _reaped(pid: int) -> bool:
    """True when nothing is left of *pid* (killed and waited for)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every language-server process ``_start`` spawns, in order."""
    pids: list[int] = []
    real_init = lsp._Client.__init__

    def counting_init(self, proc, settings):
        pids.append(proc.pid)
        real_init(self, proc, settings)

    monkeypatch.setattr(lsp._Client, "__init__", counting_init)
    monkeypatch.setattr(lsp, "server_command", _silent_server)
    return pids


@pytest.fixture
def project(tmp_path):
    (tmp_path / "a.py").write_text("def f(): pass\n\nf()\n")
    return tmp_path


def test_utf16_column_counts_code_units_not_code_points():
    line = "x = '\U0001f642'; f()"  # one emoji: 2 UTF-16 units, 1 code point
    name_at = line.index("f")

    assert lsp.utf16_column(line, name_at) == name_at + 1
    assert lsp.utf16_column("ab", 2) == 2  # ASCII is unchanged
    assert lsp.utf16_column("\u00e9", 1) == 1  # a BMP accent is one unit either way


async def test_failed_initialize_reaps_the_server_it_spawned(project, spawned, monkeypatch):
    async def no_answer(self, method, params, timeout=None):
        raise TimeoutError("no answer")

    monkeypatch.setattr(lsp._Client, "request", no_answer)
    navigator = lsp.PythonNavigator(project)

    assert await navigator.references(project / "a.py", 1, 4) is None
    assert len(spawned) == 1
    assert _reaped(spawned[0]), "the server spawned for a failed start is still running"


async def test_a_failed_start_is_not_retried_for_every_query(project, spawned, monkeypatch):
    async def no_answer(self, method, params, timeout=None):
        raise TimeoutError("no answer")

    monkeypatch.setattr(lsp._Client, "request", no_answer)
    navigator = lsp.PythonNavigator(project)

    for _ in range(3):
        assert await navigator.references(project / "a.py", 1, 4) is None
    # One timeout, not three: the next calls answer None inside the back-off.
    assert len(spawned) == 1


async def test_a_cancelled_start_still_reaps_the_server(project, spawned, monkeypatch):
    async def cancelled(self, method, params, timeout=None):
        raise asyncio.CancelledError

    monkeypatch.setattr(lsp._Client, "request", cancelled)
    navigator = lsp.PythonNavigator(project)

    with pytest.raises(asyncio.CancelledError):
        await navigator.references(project / "a.py", 1, 4)
    assert len(spawned) == 1
    assert _reaped(spawned[0]), "a stopped run left its language server behind"


async def test_a_cancelled_start_sets_no_backoff(project, spawned, monkeypatch):
    """A Stop is not a broken server: the next turn may start one again."""
    state = {"cancel_next_initialize": True}

    async def flaky(self, method, params, timeout=None):
        if method == "initialize" and state["cancel_next_initialize"]:
            state["cancel_next_initialize"] = False
            raise asyncio.CancelledError
        raise TimeoutError("no answer")

    monkeypatch.setattr(lsp._Client, "request", flaky)
    navigator = lsp.PythonNavigator(project)

    with pytest.raises(asyncio.CancelledError):
        await navigator.references(project / "a.py", 1, 4)
    assert navigator._start_failed_at is None

    assert await navigator.references(project / "a.py", 1, 4) is None
    assert len(spawned) == 2, "a cancelled start must not block the next attempt"
    assert _reaped(spawned[1])

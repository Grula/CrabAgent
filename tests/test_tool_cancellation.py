"""Mid-tool cancellation: a cancel kills the running command's whole process group.

These use real subprocesses on purpose: the point of the change is that a `sleep`
or a `pytest` started by a tool actually dies, which a mock cannot show.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools import bash as bash_tool
from crabagent.core.agent.tools import run_tests as rt
from crabagent.core.agent.tools.bash import bash_command, bash_stop
from crabagent.core.agent.tools.subprocess_cancel import (
    CANCELLED_NOTE,
    run_subprocess_cancellable,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="process groups are POSIX-only"
)

PYTEST = f"{sys.executable} -m pytest -p no:cacheprovider"


def _ctx(tmp_path):
    ctx = SimpleNamespace(
        cancel_event=asyncio.Event(),
        metadata={},
        event_bus=None,
        workspace=str(tmp_path),
        confirm_callback=None,
    )
    ctx.request_cancel = ctx.cancel_event.set
    return ctx


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _read_pid(path, deadline: float = 5.0) -> int:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            text = path.read_text().strip()
            if text:
                return int(text)
        except (OSError, ValueError):
            pass
        await asyncio.sleep(0.05)
    raise AssertionError(f"no pid was written to {path}")


async def _wait_gone(pid: int, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


# ── Normal path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_normal_command_is_unchanged(tmp_path):
    result = await bash_command("echo hello", workdir=str(tmp_path))

    assert result == "hello\n"
    assert CANCELLED_NOTE not in result


@pytest.mark.asyncio
async def test_a_normal_failure_still_reports_its_exit_code(tmp_path):
    result = await bash_command("echo oops >&2; exit 3", workdir=str(tmp_path))

    assert result == "[stderr]\noops\n\n[exit code: 3]"


# ── Cancel kills the group ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_kills_the_command_and_returns_the_marker(tmp_path):
    pid_file = tmp_path / "child.pid"
    ctx = _ctx(tmp_path)
    task = asyncio.ensure_future(
        bash_command(
            f"sh -c 'echo $$ > {pid_file}; sleep 30'",
            workdir=str(tmp_path),
            context=ctx,
        )
    )
    pid = await _read_pid(pid_file)
    assert _alive(pid)

    started = time.monotonic()
    ctx.request_cancel()
    result = await asyncio.wait_for(task, timeout=10)
    elapsed = time.monotonic() - started

    assert elapsed < 3, elapsed
    assert CANCELLED_NOTE in result
    assert "[exit code: -15]" in result  # the signal, not a traceback
    assert "Traceback" not in result
    assert await _wait_gone(pid)


@pytest.mark.asyncio
async def test_cancel_kills_a_grandchild_too(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    ctx = _ctx(tmp_path)
    task = asyncio.ensure_future(
        bash_command(
            f"sh -c 'sleep 30 & echo $! > {pid_file}; wait'",
            workdir=str(tmp_path),
            context=ctx,
        )
    )
    pid = await _read_pid(pid_file)
    assert _alive(pid)

    ctx.request_cancel()
    result = await asyncio.wait_for(task, timeout=10)

    assert CANCELLED_NOTE in result
    assert await _wait_gone(pid)


# ── Output produced before the kill is kept ───────────────────────────────────


@pytest.mark.asyncio
async def test_output_printed_before_the_cancel_is_returned(tmp_path):
    ctx = _ctx(tmp_path)
    task = asyncio.ensure_future(
        bash_command("echo before-cancel; sleep 30", workdir=str(tmp_path), context=ctx)
    )
    await asyncio.sleep(0.5)

    ctx.request_cancel()
    result = await asyncio.wait_for(task, timeout=10)

    assert "before-cancel" in result
    assert CANCELLED_NOTE in result


@pytest.mark.asyncio
async def test_the_helper_keeps_stdout_produced_before_the_kill(tmp_path):
    ctx = _ctx(tmp_path)
    task = asyncio.ensure_future(
        run_subprocess_cancellable(
            "echo early; sleep 30", shell=True, cancel_event=ctx.cancel_event
        )
    )
    await asyncio.sleep(0.5)

    ctx.cancel_event.set()
    result = await asyncio.wait_for(task, timeout=10)

    assert result.cancelled and not result.timed_out
    assert b"early" in result.stdout
    assert result.returncode is not None


# ── Timeout still works ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_timeout_still_stops_the_process(tmp_path):
    started = time.monotonic()
    result = await run_subprocess_cancellable(
        "echo started; sleep 30", shell=True, timeout=0.3
    )

    assert result.timed_out and not result.cancelled
    assert result.returncode is not None
    assert b"started" in result.stdout
    assert time.monotonic() - started < 5


@pytest.mark.asyncio
async def test_a_cancelled_test_run_kills_the_pytest_tree(tmp_path):
    probe = "test_cancellation_probe_slow"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / f"{probe}.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    )
    ctx = SimpleNamespace(
        workspace=str(tmp_path), confirm_callback=None, cancel_event=asyncio.Event()
    )
    task = asyncio.ensure_future(
        rt.run_tests(
            command=PYTEST,
            target=f"tests/{probe}.py",
            workdir=str(tmp_path),
            context=ctx,
        )
    )
    await asyncio.sleep(1.0)

    ctx.cancel_event.set()
    result = await asyncio.wait_for(task, timeout=20)

    assert result.startswith("[cancelled by user")
    await asyncio.sleep(0.3)
    found = subprocess.run(
        ["pgrep", "-af", probe], capture_output=True, text=True
    ).stdout.splitlines()
    assert [line for line in found if "pgrep" not in line] == []


# ── Cancel racing the auto-background hand-over ───────────────────────────────


def _bg_logs() -> set[Path]:
    return set(Path(tempfile.gettempdir()).glob("crabagent-bg-*.log*"))


def _drop_bg_logs(seen: set[Path]) -> None:
    for path in _bg_logs() - seen:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_cancel_during_the_timeout_window_kills_instead_of_backgrounding(tmp_path):
    """A stop that lands while the timeout hands the job over must win over backgrounding."""
    pid_file = tmp_path / "child.pid"
    logs_before = _bg_logs()
    ctx = AgentContext(workspace=tmp_path)
    task = asyncio.ensure_future(
        bash_command(
            f"sh -c 'echo $$ > {pid_file}; echo started; sleep 30'",
            workdir=str(tmp_path),
            timeout=500,  # ms: the timeout fires at 0.5 s, before the cancel below
            context=ctx,
        )
    )
    try:
        pid = await _read_pid(pid_file)
        assert _alive(pid)

        # Inside the window: the timeout already fired and _auto_background is
        # starting (it sleeps a second) — the job is not tracked yet.
        await asyncio.sleep(0.8)
        started = time.monotonic()
        ctx.request_cancel()
        result = await asyncio.wait_for(task, timeout=10)
        elapsed = time.monotonic() - started

        assert elapsed < 3, elapsed
        assert CANCELLED_NOTE in result
        assert "started" in result
        assert "[auto-background]" not in result
        assert "bash_stop(pid=" not in result
        assert "Traceback" not in result
        # No bookkeeping entry and no log for a job nobody can follow.
        assert [j for j in bash_tool._JOBS.values() if "sleep 30" in j.command] == []
        assert _bg_logs() == logs_before
        assert await _wait_gone(pid)
    finally:
        task.cancel()
        _drop_bg_logs(logs_before)


@pytest.mark.asyncio
async def test_the_plain_timeout_still_moves_the_command_to_the_background(tmp_path):
    """Without a cancel the same timeout still backgrounds the job, and bash_stop ends it."""
    logs_before = _bg_logs()
    ctx = AgentContext(workspace=tmp_path)
    result = await asyncio.wait_for(
        bash_command(
            "echo started; sleep 30",
            workdir=str(tmp_path),
            timeout=300,
            context=ctx,
        ),
        timeout=20,
    )
    pid = int(result.split("  PID: ")[1].splitlines()[0])
    try:
        assert "[auto-background]" in result
        assert "started" in result
        assert CANCELLED_NOTE not in result
        assert pid in bash_tool._JOBS
        assert _alive(pid)

        stopped = await asyncio.wait_for(bash_stop(pid, context=ctx), timeout=20)

        assert stopped.startswith(f"[PID {pid}] stopped.")
        assert await _wait_gone(pid)
    finally:
        bash_tool._JOBS.pop(pid, None)
        _drop_bg_logs(logs_before)

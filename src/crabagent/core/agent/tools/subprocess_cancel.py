"""Run a subprocess in its own process group so a cancel can kill the whole tree.

The agent loop cancels cooperatively through ``AgentContext.cancel_event``. A tool
that only checks that flag between steps leaves a running ``bash`` or ``pytest``
behind: the shell survives, and so do the processes it spawned. Every process
started here leads its own session/process group (``start_new_session=True``), so
signalling the group reaches the grandchildren too.

``run_subprocess_cancellable`` is the whole-job helper (spawn, wait, kill, report).
``spawn_cancellable`` + ``watch_cancel`` are the pieces a tool needs when it wants
to read the output itself while the command runs (bash streams it live).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

IS_WINDOWS = sys.platform == "win32"

# Appended to a tool result when the user stopped the run.
CANCELLED_NOTE = "[cancelled by user — process group terminated]"

# How often the canceller wakes up to look at the event, in seconds.
_POLL_SECONDS = 0.1
# How long a signalled group has to exit on SIGTERM before it is SIGKILLed.
_SIGTERM_GRACE = 2.0
# How long to wait for the pipes to drain once the group is dead.
_DRAIN_GRACE = 5.0

# Same shell choice as bash.py: models write bash syntax, so prefer bash.
_POSIX_SHELL = None if IS_WINDOWS else shutil.which("bash")


@dataclass
class SubprocessResult:
    """What a finished (or stopped) subprocess left behind."""

    stdout: bytes = b""
    stderr: bytes = b""
    returncode: int | None = None
    cancelled: bool = False
    timed_out: bool = False
    # Set when the process could not even be started; the helper never raises.
    start_error: str = ""


def subprocess_kwargs() -> dict[str, Any]:
    """Kwargs that put the child in its own process group (and pick the shell)."""
    if IS_WINDOWS:
        return {
            "creationflags": (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        }
    kwargs: dict[str, Any] = {"start_new_session": True}
    if _POSIX_SHELL:
        kwargs["executable"] = _POSIX_SHELL
    return kwargs


def process_group_id(proc: Any) -> int | None:
    """The process group *proc* leads, or None where process groups are unavailable."""
    pgid = getattr(proc, "_cancellable_pgid", None)
    if pgid is not None:
        return pgid
    if not (hasattr(os, "killpg") and hasattr(os, "getpgid")):
        return None
    try:
        return os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return None


def signal_process_group(proc: Any, *, kill: bool) -> None:
    """Signal *proc*'s whole process group; falls back to the child off POSIX."""
    pgid = process_group_id(proc)
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL if kill else signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        return
    try:
        proc.kill() if kill else proc.terminate()
    except (ProcessLookupError, OSError):
        pass


async def terminate_process_group(proc: Any, *, grace: float = _SIGTERM_GRACE) -> None:
    """SIGTERM the group, SIGKILL it after *grace* seconds, and reap the child.

    The SIGKILL sweep runs even when the leader already exited: a grandchild that
    ignores SIGTERM would otherwise outlive the run. Never raises.
    """
    if proc.returncode is None:
        signal_process_group(proc, kill=False)
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace)
        except (asyncio.TimeoutError, OSError):
            pass
    signal_process_group(proc, kill=True)
    try:
        await proc.wait()
    except Exception:  # already reaped, or the transport is gone
        pass


async def spawn_cancellable(argv: Any, *, shell: bool = False, **kwargs: Any) -> Any:
    """Start a process in its own process group; pipes and DEVNULL stdin by default."""
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("stdout", asyncio.subprocess.PIPE)
    kwargs.setdefault("stderr", asyncio.subprocess.PIPE)
    for key, value in subprocess_kwargs().items():
        kwargs.setdefault(key, value)
    if shell:
        proc = await asyncio.create_subprocess_shell(argv, **kwargs)
    else:
        proc = await asyncio.create_subprocess_exec(*argv, **kwargs)
    # Remember the group while the leader is alive: getpgid() fails once it is reaped.
    pgid = None
    if not IS_WINDOWS and hasattr(os, "getpgid"):
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            pgid = None
    if pgid:
        proc._cancellable_pgid = pgid  # type: ignore[attr-defined]
    return proc


async def watch_cancel(proc: Any, cancel_event: Any, *, poll: float = _POLL_SECONDS) -> bool:
    """Kill *proc*'s group as soon as *cancel_event* is set; True if it did."""
    if cancel_event is None:
        return False
    while proc.returncode is None:
        if cancel_event.is_set():
            await terminate_process_group(proc)
            return True
        await asyncio.sleep(poll)
    return False


async def run_subprocess_cancellable(
    argv: Any,
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    cancel_event: Any = None,
    env: dict[str, str] | None = None,
    shell: bool = False,
    **spawn_kwargs: Any,
) -> SubprocessResult:
    """Run *argv* to completion, killing its whole process group on cancel or timeout.

    ``timeout`` is in seconds. Output produced before the kill is kept. Never
    raises: a failure to start or to collect output is reported through the
    result's flags and ``start_error``.
    """
    result = SubprocessResult()
    try:
        proc = await spawn_cancellable(
            argv, cwd=cwd, env=env, shell=shell, **spawn_kwargs
        )
    except Exception as exc:  # the caller gets a result, not a traceback
        result.start_error = f"{type(exc).__name__}: {exc}"
        result.stderr = result.start_error.encode("utf-8", errors="replace")
        return result

    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + timeout
    communicate = asyncio.ensure_future(proc.communicate())
    out: bytes = b""
    err: bytes = b""
    try:
        while not communicate.done():
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                break
            if deadline is not None and loop.time() >= deadline:
                result.timed_out = True
                break
            await asyncio.wait({communicate}, timeout=_POLL_SECONDS)
        if result.cancelled or result.timed_out:
            await terminate_process_group(proc)
        try:
            gathered, errors = await asyncio.wait_for(communicate, timeout=_DRAIN_GRACE)
            out = gathered if isinstance(gathered, bytes) else b""
            err = errors if isinstance(errors, bytes) else b""
        except Exception:  # a survivor holding the pipe open; keep no partial bytes
            communicate.cancel()
    finally:
        if not communicate.done():
            communicate.cancel()
            try:
                await asyncio.wait_for(proc.wait(), timeout=_DRAIN_GRACE)
            except Exception:
                pass

    result.stdout = out
    result.stderr = err
    result.returncode = proc.returncode
    return result

import asyncio
import glob
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from crabagent.core.agent.tools.path_utils import get_workspace_root
from crabagent.core.agent.tools.registry import registry
from crabagent.core.agent.tools.sandbox import truncate_output, validate_command
from crabagent.core.agent.tools.subprocess_cancel import (
    CANCELLED_NOTE,
    spawn_cancellable,
    subprocess_kwargs,
    terminate_process_group,
    watch_cancel,
)
from crabagent.core.agent.tools.text_encoding import StreamDecoder, windows_output_encoding

logger = logging.getLogger(__name__)

# ── Platform detection ─────────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"

# Emit BASH_OUTPUT events at most every N seconds
_EMIT_INTERVAL = 0.15

# When reading stream chunks, timeout per individual read() call.
# Prevents hanging if the process stalls without producing output.
_CHUNK_READ_TIMEOUT = 60

_ENCODING = windows_output_encoding()


# ── Background job tracking ────────────────────────────────────────────
# Commands started with background=true, or moved to the background after a
# timeout, are recorded here so bash_output / bash_stop can follow and stop
# them. The registry lives in this process only: jobs started before a server
# restart keep running but are no longer tracked.


@dataclass
class BackgroundJob:
    pid: int
    command: str
    log_file: str
    scope: str  # session id of the conversation that started it ("" outside a session)
    started_at: float
    exit_file: str = ""  # explicit background jobs on Unix write their exit code here
    proc: Any = None  # asyncio process handle for auto-backgrounded commands
    start_marker: str = ""  # OS start time of the process; guards against PID reuse
    read_offset: int = 0  # bytes of the log already returned by bash_output
    exit_code: int | None = None  # remembered once known, so it survives deleting the exit file
    drains: list = field(default_factory=list)  # tasks still copying an auto-backgrounded command's output
    log_removed: bool = False  # all output was handed back and the log files were deleted


_JOBS: dict[int, BackgroundJob] = {}
_MAX_TRACKED_JOBS = 200
_MAX_WAIT_SECONDS = 60
# A log that no tracked job owns and that nothing has written to for this long is
# left over from an earlier server run (or from output nobody read) and is deleted.
_STALE_LOG_SECONDS = 24 * 3600


def _job_files(job: BackgroundJob) -> list[str]:
    files = [job.log_file, job.log_file + ".err"]  # .err: stderr log on Windows
    if job.exit_file:
        files.append(job.exit_file)
    return files


def _remove_logs(job: BackgroundJob) -> bool:
    """Delete a finished job's log files once all its output has been handed back.

    The job stays tracked, so bash_output and bash_stop still report on it.
    """
    if job.log_removed:
        return True
    if any(not d.done() for d in job.drains):
        return False  # output is still being copied into the log
    for path in _job_files(job):
        try:
            os.remove(path)
        except OSError:
            pass
    job.log_removed = True
    return True


def _sweep_stale_logs() -> None:
    tracked = {path for job in _JOBS.values() for path in _job_files(job)}
    now = time.time()
    for path in glob.glob(os.path.join(tempfile.gettempdir(), "crabagent-bg-*")):
        if path in tracked:
            continue
        try:
            if now - os.path.getmtime(path) > _STALE_LOG_SECONDS:
                os.remove(path)
        except OSError:
            pass


def _job_scope(context: Any) -> str:
    metadata = getattr(context, "metadata", None)
    if isinstance(metadata, dict):
        return str(metadata.get("session_id") or "")
    return ""


async def _process_start_marker(pid: int) -> str:
    """Return the OS-reported start time of *pid* ("" when unknown or on Windows)."""
    if IS_WINDOWS:
        return ""
    try:
        check = await asyncio.create_subprocess_shell(
            f"ps -p {int(pid)} -o lstart=",
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **_build_subprocess_kwargs(),
        )
        out, _ = await asyncio.wait_for(check.communicate(), timeout=5)
        return out.decode(_ENCODING, errors="replace").strip()
    except Exception:
        return ""


async def _register_job(
    pid: int | str,
    command: str,
    log_file: str,
    context: Any,
    *,
    exit_file: str = "",
    proc: Any = None,
) -> None:
    try:
        pid_int = int(str(pid).strip())
    except ValueError:
        return
    _sweep_stale_logs()
    if len(_JOBS) >= _MAX_TRACKED_JOBS:
        # Forget the oldest entries; their processes are unaffected.
        for old_pid in sorted(_JOBS, key=lambda p: _JOBS[p].started_at)[: len(_JOBS) - _MAX_TRACKED_JOBS + 1]:
            _JOBS.pop(old_pid, None)
    _JOBS[pid_int] = BackgroundJob(
        pid=pid_int,
        command=command,
        log_file=log_file,
        scope=_job_scope(context),
        started_at=time.time(),
        exit_file=exit_file,
        proc=proc,
        start_marker=await _process_start_marker(pid_int),
    )


async def _job_state(job: BackgroundJob) -> tuple[bool, int | None]:
    """Return ``(running, exit_code)`` for *job*; exit_code is None when unknown."""
    if job.exit_code is not None:
        return False, job.exit_code
    if job.proc is not None:
        code = getattr(job.proc, "returncode", None)
        job.exit_code = code
        return code is None, code

    exit_code: int | None = None
    if job.exit_file:
        try:
            with open(job.exit_file, encoding="utf-8") as fh:
                exit_code = int(fh.read().strip())
        except (OSError, ValueError):
            exit_code = None
    if exit_code is not None:
        job.exit_code = exit_code
        return False, exit_code

    if not await _check_process_alive(job.pid):
        return False, None
    if job.start_marker and await _process_start_marker(job.pid) != job.start_marker:
        return False, None  # the PID now belongs to a different process
    return True, None


def _read_new_output(job: BackgroundJob, max_chars: int) -> str:
    """Return log text written since the last call and advance the read position."""
    return _read_output(job, max_chars)[0]


def _read_output(job: BackgroundJob, max_chars: int) -> tuple[str, bool]:
    """Like _read_new_output, plus whether all of the new text was returned (nothing cut)."""
    if job.log_removed:
        return "", True
    try:
        size = os.path.getsize(job.log_file)
        if size < job.read_offset:  # log was truncated or replaced
            job.read_offset = 0
        with open(job.log_file, "rb") as fh:
            fh.seek(job.read_offset)
            data = fh.read()
        job.read_offset += len(data)
    except OSError:
        return "", True
    text = data.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        omitted = len(text) - max_chars
        text = f"[... {omitted} earlier characters omitted; the full log is at {job.log_file}]\n" + text[-max_chars:]
        return text, False
    return text, True


def _log_note(job: BackgroundJob) -> str:
    return "log: deleted, all output shown" if job.log_removed else f"log: {job.log_file}"


def _format_runtime(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _describe_state(running: bool, exit_code: int | None) -> str:
    if running:
        return "running"
    return f"exited (exit code {exit_code})" if exit_code is not None else "exited"


def _find_job(pid: Any, context: Any) -> tuple[BackgroundJob | None, str]:
    try:
        pid_int = int(str(pid).strip())
    except ValueError:
        return None, f"Error: pid must be a number, got {pid!r}."
    job = _JOBS.get(pid_int)
    if job is None or job.scope != _job_scope(context):
        return None, (
            f"Error: no background command with PID {pid_int} was started in this session. "
            "Call bash_output without a pid to list the ones that were."
        )
    return job, ""


# The tool is called "bash" and models write bash (pipefail, PIPESTATUS, [[ ]],
# arrays, `source`, <( )). Without this, /bin/sh runs it, which is dash on Debian,
# Ubuntu and WSL and rejects all of those. Falls back to /bin/sh without bash.
_POSIX_SHELL = None if sys.platform == "win32" else shutil.which("bash")


def _build_subprocess_kwargs(workdir: str | None = None) -> dict:
    """Build platform-appropriate kwargs for subprocess creation."""
    kwargs = subprocess_kwargs()
    if workdir:
        kwargs["cwd"] = workdir
    return kwargs


def with_user_profile(command: str) -> str:
    """The command, preceded on Unix by sourcing the user's profile (for PATH: homebrew etc.).

    On Windows profiles don't apply, so the command runs as given.
    """
    if IS_WINDOWS:
        return command
    return (
        "export SHELL=$(echo $SHELL 2>/dev/null || echo /bin/sh); "
        "[ -f ~/.zprofile ] && . ~/.zprofile; "
        "[ -f ~/.bash_profile ] && . ~/.bash_profile; "
        "[ -f ~/.profile ] && . ~/.profile; "
    ) + command


def _get_subprocess_env() -> dict[str, str]:
    """Return an environment dict that encourages UTF-8 output."""
    env = os.environ.copy()
    if IS_WINDOWS:
        # Force Python child processes to use UTF-8, and set the console
        # codepage to 65001 (UTF-8).  Non-Python programs may still emit
        # the OEM codepage, which _ENCODING handles.
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
    return env


async def _emit_bash_output(context: Any, text: str, tool_call_id: str = "") -> None:
    """Emit a BASH_OUTPUT SSE event for real-time frontend display."""
    if not context or not hasattr(context, "event_bus") or not context.event_bus:
        return
    from crabagent.core.event import AgentEvent, EventType

    await context.event_bus.emit(
        AgentEvent(
            type=EventType.BASH_OUTPUT,
            data={"text": text, "tool_call_id": tool_call_id},
        )
    )


if IS_WINDOWS:
    _TOOL_DESCRIPTION = (
        "Execute a shell command (Windows cmd.exe / PowerShell). "
        "For long-running services (dev servers, watchers), "
        "set background=true to run in background and return immediately; "
        "then follow it with bash_output and stop it with bash_stop. "
        "Use Windows commands: dir, type, tasklist, taskkill, etc."
    )
else:
    _TOOL_DESCRIPTION = (
        "Execute a bash command. For long-running services (dev servers, watchers), "
        "set background=true to run in background and return immediately; "
        "then follow it with bash_output and stop it with bash_stop. "
        "Do NOT use osascript or open new Terminal windows."
    )


@registry.register(
    name="bash",
    description=_TOOL_DESCRIPTION,
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for the command. Default: the workspace (project) directory.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in milliseconds. Default: 120000.",
                "default": 120000,
            },
            "background": {
                "type": "boolean",
                "description": (
                    "Set true for long-running commands (dev servers, watchers, etc.). "
                    "Runs in background, returns immediately with PID."
                ),
                "default": False,
            },
        },
        "required": ["command"],
    },
    requires_permission=True,
)
async def bash_command(
    command: str,
    workdir: str | None = None,
    timeout: int = 120000,
    background: bool = False,
    context: Any = None,
) -> str:
    block_reason = validate_command(command)
    if block_reason:
        if block_reason.startswith("NEED_CONFIRM:"):
            detail = block_reason[len("NEED_CONFIRM:"):]
            if context and context.confirm_callback:
                approved = await context.confirm_callback("bash", {
                    "command": command,
                    "warning": detail,
                })
                if not approved:
                    return f"Command denied by user: {detail}"
            else:
                return f"⚠️ Safety notice: this command needs your confirmation — {detail}\n\nAllow execution? Please reply yes/no."
        else:
            return block_reason

    if not workdir:
        root = get_workspace_root(context)
        if root:
            workdir = str(root)

    if background:
        return await _run_background(command, workdir, context)

    full_command = with_user_profile(command)

    try:
        proc = await spawn_cancellable(
            full_command,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_get_subprocess_env(),
            **_build_subprocess_kwargs(workdir),
        )

        timeout_sec = timeout / 1000.0
        collected_stdout: list[str] = []
        collected_stderr: list[str] = []

        # Try to extract tool_call_id from context for SSE event correlation
        tool_call_id = ""
        if context and hasattr(context, "metadata"):
            tool_call_id = context.metadata.get("current_tool_call_id", "")

        # A cancel kills the command's whole process group while it runs, so a
        # long sleep, a dev server or a test run stops now instead of at the
        # next tool boundary.
        cancel_event = getattr(context, "cancel_event", None)
        watcher = (
            asyncio.ensure_future(watch_cancel(proc, cancel_event))
            if cancel_event is not None
            else None
        )

        try:
            await asyncio.wait_for(
                _stream_output(
                    proc,
                    collected_stdout,
                    collected_stderr,
                    context,
                    tool_call_id,
                ),
                timeout=timeout_sec,
            )
            # Process completed, or its group was killed by a cancel
            await proc.wait()
            full_out = "".join(collected_stdout)
            full_err = "".join(collected_stderr)
            if watcher is not None and cancel_event.is_set():
                await _settle_cancel(watcher)
                return _format_result(
                    full_out, full_err, proc.returncode, note=CANCELLED_NOTE
                )
            return _format_result(full_out, full_err, proc.returncode)

        except TimeoutError:
            # Process still running after timeout → auto-background, unless the
            # user stopped the run: then the cancel wins and the group is killed.
            return await _auto_background(
                proc,
                collected_stdout,
                collected_stderr,
                command,
                timeout_sec,
                context,
                watcher=watcher,
            )
        finally:
            if watcher is not None and not watcher.done():
                watcher.cancel()

    except Exception as e:
        return f"Error executing command: {e}"


async def _stream_output(
    proc: asyncio.subprocess.Process,
    stdout_sink: list[str],
    stderr_sink: list[str],
    context: Any,
    tool_call_id: str = "",
) -> None:
    """Read stdout/stderr in chunks, emit BASH_OUTPUT events in real-time."""

    async def _read_stream(
        stream: asyncio.StreamReader,
        sink: list[str],
        label: str,
    ) -> None:
        buf = ""
        decoder = StreamDecoder(_ENCODING)
        last_emit = 0.0
        while True:
            try:
                chunk = await asyncio.wait_for(
                    stream.read(4096), timeout=_CHUNK_READ_TIMEOUT
                )
            except TimeoutError:
                # No data for a while, but process may still be running
                # Emit whatever we have buffered
                if buf:
                    await _emit_bash_output(context, buf, tool_call_id)
                    buf = ""
                    last_emit = time.monotonic()
                continue
            except Exception:
                break

            if not chunk:
                break

            text = decoder.decode(chunk)
            sink.append(text)
            buf += text

            now = time.monotonic()
            # Emit when buffer accumulates enough or enough time has passed
            if len(buf) >= 4096 or (now - last_emit) >= _EMIT_INTERVAL:
                await _emit_bash_output(context, buf, tool_call_id)
                buf = ""
                last_emit = now

        tail = decoder.decode(b"", final=True)
        sink.append(tail)
        buf += tail
        # Flush remaining buffer
        if buf:
            await _emit_bash_output(context, buf, tool_call_id)

    await asyncio.gather(
        _read_stream(proc.stdout, stdout_sink, "stdout"),
        _read_stream(proc.stderr, stderr_sink, "stderr"),
    )


def _format_result(
    stdout: str,
    stderr: str,
    returncode: int,
    timed_out: bool = False,
    timeout_sec: float = 0,
    note: str = "",
) -> str:
    """Format command output into a string result."""
    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if returncode != 0:
        parts.append(f"[exit code: {returncode}]")
    if timed_out:
        parts.append(f"[timed out after {timeout_sec:.0f}s]")
    if note:
        parts.append(note)
    output = "\n".join(parts) if parts else "[no output]"
    return truncate_output(output)


async def _settle_cancel(watcher: asyncio.Task | None, limit: float = 10.0) -> None:
    """Wait (bounded) until the cancel watcher has killed and reaped the group."""
    if watcher is None or watcher.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(watcher), timeout=limit)
    except Exception:
        pass


def _cancel_pending(context: Any, cancel_event: Any = None) -> bool:
    """True once the user stopped the run (the loop sets the event on the context)."""
    event = cancel_event if cancel_event is not None else getattr(context, "cancel_event", None)
    if event is not None:
        try:
            if event.is_set():
                return True
        except Exception:  # a stub context without the event API
            pass
    return getattr(context, "cancelled", False) is True


async def _cancelled_result(
    proc: asyncio.subprocess.Process,
    watcher: asyncio.Task | None,
    collected_stdout: list[str],
    collected_stderr: list[str],
) -> str:
    """Kill *proc*'s group (the watcher does it when it still can) and report a cancel."""
    if watcher is not None and not watcher.done():
        await _settle_cancel(watcher)
    if proc.returncode is None:
        await terminate_process_group(proc)
    code = proc.returncode if proc.returncode is not None else -1
    return _format_result(
        "".join(collected_stdout),
        "".join(collected_stderr),
        code,
        note=CANCELLED_NOTE,
    )


# ── Platform-specific helpers ──────────────────────────────────────────


async def _check_process_alive(pid: int | str) -> str:
    """Check if a process is still running. Returns command-line or empty."""
    if IS_WINDOWS:
        cmd = f'tasklist /FI "PID eq {pid}" /FO CSV /NH'
    else:
        cmd = f"ps -p {pid} -o pid=,command="
    try:
        check = await asyncio.create_subprocess_shell(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **_build_subprocess_kwargs(),
        )
        out, _ = await asyncio.wait_for(check.communicate(), timeout=5)
        text = out.decode(_ENCODING, errors="replace").strip()
        if IS_WINDOWS:
            # tasklist output: "python.exe","1234","Console","1","1,234 KB"
            # Return the image name
            if text and "INFO:" not in text:
                return text.split(",")[0].strip('"')
            return ""
        return text
    except Exception:
        return ""


async def _auto_background(
    proc: asyncio.subprocess.Process,
    collected_stdout: list[str],
    collected_stderr: list[str],
    command: str,
    timeout_sec: float,
    context: Any,
    watcher: asyncio.Task | None = None,
) -> str:
    """Process exceeded timeout — redirect remaining output to a log file and return."""

    pid = proc.pid

    # A cancel that raced with the timeout wins: the user asked for the command to
    # stop, so the group is killed here instead of being handed to the background
    # manager (which knows nothing about the cancel and would let it keep running).
    if _cancel_pending(context):
        return await _cancelled_result(proc, watcher, collected_stdout, collected_stderr)

    log_dir = tempfile.gettempdir()
    log_file = os.path.join(log_dir, f"crabagent-bg-{pid}.log")

    # Write already-collected output to log file
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("".join(collected_stdout))
            if collected_stderr:
                f.write("\n[stderr]\n")
                f.write("".join(collected_stderr))
    except Exception:
        pass
    try:
        already_logged = os.path.getsize(log_file)
    except OSError:
        already_logged = 0

    # Redirect process remaining output to log file (append mode)
    drains = []
    for stream, prefix in ((proc.stdout, b""), (proc.stderr, b"[stderr] ")):
        if not stream:
            continue
        try:
            log_fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        except OSError:
            continue
        drain = _drain_to_fd(stream, log_fd, prefix=prefix)
        try:
            drains.append(asyncio.ensure_future(drain))
        except Exception:
            drain.close()
            os.close(log_fd)

    async def _hand_back_cancelled() -> str:
        """Kill the group, forget the job and drop the log the hand-over created."""
        for drain in drains:
            drain.cancel()
        _JOBS.pop(pid, None)
        result = await _cancelled_result(
            proc, watcher, collected_stdout, collected_stderr
        )
        try:
            os.unlink(log_file)
        except OSError:
            pass
        return result

    # Check if process is still alive
    await asyncio.sleep(1)
    alive_info = await _check_process_alive(pid)
    status_line = f"  command: {alive_info}\n" if alive_info else ""

    # The cancel can land while the hand-over above is starting (those checks take
    # seconds); re-check before anything tells the model the job moved.
    if _cancel_pending(context):
        return await _hand_back_cancelled()

    # Emit final event to inform frontend
    try:
        tool_call_id = ""
        if context and hasattr(context, "metadata"):
            tool_call_id = context.metadata.get("current_tool_call_id", "")
        await _emit_bash_output(
            context,
            (
                f"\n⏳ Command exceeded {timeout_sec:.0f}s and was moved to the background\n"
                f"   PID: {pid}\n"
                f"   Log: {log_file}\n"
            ),
            tool_call_id,
        )
    except Exception:
        pass

    partial_out = "".join(collected_stdout)
    partial_err = "".join(collected_stderr)
    result = _format_result(partial_out, partial_err, -1)

    # Track the job so bash_output / bash_stop can follow it. The output shown
    # above is already in the log, so later reads start after it.
    await _register_job(pid, command, log_file, context, proc=proc)
    job = _JOBS.get(pid) if isinstance(pid, int) else None
    if job is not None:
        job.read_offset = already_logged
        job.drains = drains  # the log is complete only once these finish

    # Registering is another await; a cancel in that window must not leave a
    # tracked job (or an "it moved to the background" answer) behind.
    if _cancel_pending(context):
        return await _hand_back_cancelled()

    return (
        f"{result}\n"
        f"[auto-background] Command still running after {timeout_sec:.0f}s; moved to the background\n"
        f"  PID: {pid}\n"
        f"{status_line}"
        f"  Log: {log_file}\n"
        f"  Follow it: bash_output(pid={pid})\n"
        f"  Stop it: bash_stop(pid={pid})\n"
    )


async def _drain_to_fd(
    stream: asyncio.StreamReader,
    fd: int,
    max_bytes: int = 500_000,
    prefix: bytes = b"",
) -> None:
    """Drain remaining stream output to a file descriptor."""
    collected = 0
    wrote_prefix = not prefix  # Skip prefix on first write if empty
    try:
        while collected < max_bytes:
            try:
                chunk = await asyncio.wait_for(stream.read(4096), timeout=5)
            except Exception:
                break
            if not chunk:
                break
            if not wrote_prefix:
                os.write(fd, prefix)
                wrote_prefix = True
            os.write(fd, chunk)
            collected += len(chunk)
    except Exception:
        pass
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


async def _run_background(
    command: str, workdir: str | None = None, context: Any = None
) -> str:
    """Start command in background and return immediately."""

    # One log per command. (Naming it after this process's PID made every
    # background command of a server share, and overwrite, the same file.)
    log_dir = tempfile.gettempdir()
    log_file = os.path.join(log_dir, f"crabagent-bg-{uuid.uuid4().hex[:12]}.log")
    exit_file = ""

    if IS_WINDOWS:
        # Windows: use START /B to launch in background.
        # We run a small wrapper via cmd.exe that echoes the PID.
        # Use PowerShell for reliable PID capture.
        wrapped = (
            f'$ErrorActionPreference="SilentlyContinue"; '
            f'$p = Start-Process -FilePath "cmd.exe" '
            f'-ArgumentList "/c {command}" '
            f'-WindowStyle Hidden -PassThru '
            f'-RedirectStandardOutput "{log_file}" '
            f'-RedirectStandardError "{log_file}.err"; '
            f'Write-Output $p.Id'
        )
        shell_executable = "powershell"
        shell_prefix = ["-NoProfile", "-Command", wrapped]
    else:
        _profile_cmd = (
            "export SHELL=$(echo $SHELL 2>/dev/null || echo /bin/sh); "
            "[ -f ~/.zprofile ] && . ~/.zprofile; "
            "[ -f ~/.bash_profile ] && . ~/.bash_profile; "
            "[ -f ~/.profile ] && . ~/.profile; "
        )
        # The command runs in its own subshell so that `exit`, `cd` or a heredoc
        # inside it cannot disturb the wrapper, which records the exit code once
        # the command finishes. Output of the whole group goes to the log, and
        # `$!` is the PID of the backgrounded group.
        exit_file = f"{log_file}.exit"
        wrapped = (
            "{ (\n"
            f"{_profile_cmd}{command}\n"
            f"); echo $? > {shlex.quote(exit_file)}; }} > {shlex.quote(log_file)} 2>&1 & echo $!"
        )
        shell_executable = None
        shell_prefix = None

    try:
        spawn_kwargs = _build_subprocess_kwargs(workdir)
        spawn_kwargs["stdin"] = subprocess.DEVNULL
        spawn_kwargs["stdout"] = asyncio.subprocess.PIPE
        spawn_kwargs["stderr"] = asyncio.subprocess.PIPE
        spawn_kwargs["env"] = _get_subprocess_env()

        if IS_WINDOWS:
            # Use create_subprocess_exec to invoke powershell explicitly
            proc = await asyncio.create_subprocess_exec(
                shell_executable, *shell_prefix,
                **spawn_kwargs,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                wrapped,
                **spawn_kwargs,
            )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

        # The PID is the last line the launcher printed.
        out_lines = stdout.decode(_ENCODING, errors="replace").strip().splitlines()
        pid_str = out_lines[-1].strip() if out_lines else ""
        err_str = stderr.decode(_ENCODING, errors="replace").strip()

        if proc.returncode != 0 or not pid_str.isdigit():
            return f"[background] failed to start\n{err_str}"

        await _register_job(pid_str, command, log_file, context, exit_file=exit_file)
        job = _JOBS.get(int(pid_str))

        # Emit event for frontend
        await _emit_bash_output(
            context,
            f"[background] PID={pid_str} — process started\n  log: {log_file}\n",
        )

        await asyncio.sleep(1)
        alive_info = await _check_process_alive(pid_str)
        if alive_info:
            return (
                f"[background] PID={pid_str} — process is running\n"
                f"  command: {alive_info}\n"
                f"  log: {log_file}\n"
                f"  Follow it: bash_output(pid={pid_str})\n"
                f"  Stop it: bash_stop(pid={pid_str})"
            )

        # Already finished: report the result now so no follow-up call is needed.
        if job is None:
            return f"[background] PID={pid_str} — already finished\n  log: {log_file}"
        _, exit_code = await _job_state(job)
        output, complete = _read_output(job, 10_000)
        if complete:
            _remove_logs(job)
        return (
            f"[background] PID={pid_str} — already {_describe_state(False, exit_code)}\n"
            f"  {_log_note(job)}\n"
            f"{output.rstrip() or '[no output]'}"
        )
    except TimeoutError:
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=3)
        except Exception:
            pass
        return "[background] error: shell timed out starting background process"
    except Exception as e:
        return f"[background] error: {e}"


# ── Following and stopping background commands ─────────────────────────


@registry.register(
    name="bash_output",
    description=(
        "Check on commands that bash started in the background (background=true, or moved there after a "
        "timeout). With a pid: returns whether it is still running, its exit code if known, and the output "
        "written since your last check. Without a pid: lists this session's background commands. "
        "Set wait_seconds to wait for new output or for the command to finish instead of polling."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pid": {
                "type": "integer",
                "description": "PID reported by bash when the command went to the background. Omit to list all.",
            },
            "wait_seconds": {
                "type": "integer",
                "description": (
                    "Wait up to this many seconds (max 60) for new output or for the command to exit. "
                    "Default: 0, return immediately."
                ),
                "default": 0,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters of new output to return; the newest are kept. Default: 10000.",
                "default": 10000,
            },
        },
    },
)
async def bash_output(
    pid: int | str | None = None,
    wait_seconds: int = 0,
    max_chars: int = 10_000,
    context: Any = None,
) -> str:
    if pid is None or str(pid).strip() == "":
        scope = _job_scope(context)
        jobs = sorted((j for j in _JOBS.values() if j.scope == scope), key=lambda j: j.started_at)
        if not jobs:
            return "No background commands have been started in this session."
        now = time.time()
        lines = []
        for job in jobs:
            running, exit_code = await _job_state(job)
            command = job.command if len(job.command) <= 100 else job.command[:97] + "..."
            lines.append(
                f"PID {job.pid} | {_describe_state(running, exit_code)} | "
                f"started {_format_runtime(now - job.started_at)} ago | {command}"
            )
        return "\n".join(lines)

    job, error = _find_job(pid, context)
    if job is None:
        return error

    max_chars = max(200, min(int(max_chars or 10_000), 50_000))
    deadline = time.monotonic() + max(0, min(int(wait_seconds or 0), _MAX_WAIT_SECONDS))
    while True:
        running, exit_code = await _job_state(job)
        try:
            has_new_output = os.path.getsize(job.log_file) > job.read_offset
        except OSError:
            has_new_output = False
        if has_new_output or not running or time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.5)

    output, complete = _read_output(job, max_chars)
    if not running and complete:
        _remove_logs(job)  # finished and fully read: the log has nothing left to give
    header = (
        f"[PID {job.pid}] {_describe_state(running, exit_code)} | "
        f"started {_format_runtime(time.time() - job.started_at)} ago | {_log_note(job)}"
    )
    output = output.rstrip()
    return f"{header}\n{output or '[no new output]'}"


async def _terminate(job: BackgroundJob) -> None:
    """Ask the job's whole process tree to exit, then force it if it does not."""
    if IS_WINDOWS:
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(job.pid), "/T", "/F",
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await asyncio.wait_for(killer.wait(), timeout=10)
        return

    def _signal(sig: int) -> None:
        # Background commands run in their own session, so the process group
        # holds the command and everything it spawned.
        try:
            group = os.getpgid(job.pid)
        except OSError:
            group = None
        if group is not None and group != os.getpgid(0):
            os.killpg(group, sig)
        else:
            os.kill(job.pid, sig)

    _signal(signal.SIGTERM)
    for _ in range(30):  # up to 3 seconds for a clean shutdown
        await asyncio.sleep(0.1)
        if not (await _job_state(job))[0]:
            return
    _signal(signal.SIGKILL)


@registry.register(
    name="bash_stop",
    description=(
        "Stop a command that bash started in the background, together with the processes it spawned. "
        "Use it to shut down dev servers and watchers you started once you no longer need them. "
        "Only works on background commands started in this session."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pid": {
                "type": "integer",
                "description": "PID reported by bash when the command went to the background.",
            },
        },
        "required": ["pid"],
    },
)
async def bash_stop(pid: int | str, context: Any = None) -> str:
    job, error = _find_job(pid, context)
    if job is None:
        return error

    running, exit_code = await _job_state(job)
    if not running:
        tail, complete = _read_output(job, 2_000)
        if complete:
            _remove_logs(job)
        message = f"[PID {job.pid}] already {_describe_state(False, exit_code)}; nothing to stop."
        return f"{message}\n{tail.rstrip()}" if tail.strip() else message

    try:
        await _terminate(job)
    except ProcessLookupError:
        pass  # exited between the check and the signal
    except Exception as e:
        return f"Error stopping PID {job.pid}: {e}"

    await asyncio.sleep(0.2)
    running, exit_code = await _job_state(job)
    if running:
        return f"[PID {job.pid}] did not exit after being signalled; it may need manual attention."
    tail, complete = _read_output(job, 2_000)
    if complete:
        _remove_logs(job)
    tail = tail.rstrip()
    return f"[PID {job.pid}] stopped.\n{tail}" if tail else f"[PID {job.pid}] stopped."

"""Tests for the bash command execution tool."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools import bash as bash_tool
from crabagent.core.agent.tools.bash import bash_command, _format_result


# ── _format_result ────────────────────────────────────────────────────


class TestFormatResult:
    def test_stdout_only(self):
        result = _format_result("hello", "", 0)
        assert result == "hello"

    def test_stdout_and_stderr(self):
        result = _format_result("out", "err", 0)
        assert "out" in result
        assert "[stderr]" in result
        assert "err" in result

    def test_nonzero_exit_code(self):
        result = _format_result("", "", 1)
        assert "[exit code: 1]" in result

    def test_no_output(self):
        result = _format_result("", "", 0)
        assert result == "[no output]"

    def test_timed_out(self):
        result = _format_result("partial", "", -1, timed_out=True, timeout_sec=30)
        assert "[timed out after 30s]" in result

    def test_truncates_long_output(self):
        long = "A" * 100000
        result = _format_result(long, "", 0)
        assert len(result) < 60000
        assert "truncated" in result


# ── validate_command integration ──────────────────────────────────────


class TestBashValidation:
    @pytest.mark.asyncio
    async def test_blocked_destructive_command_returns_error(self, tmp_path: Path):
        context = AgentContext(workspace=tmp_path)
        result = await bash_command("rm -rf /", context=context)
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_blocked_privilege_escalation(self, tmp_path: Path):
        context = AgentContext(workspace=tmp_path)
        result = await bash_command("sudo ls", context=context)
        assert "blocked" in result.lower() or "privilege" in result.lower()

    @pytest.mark.asyncio
    async def test_need_confirm_without_callback_returns_warning(self, tmp_path: Path):
        context = AgentContext(workspace=tmp_path)
        # Writing to /etc/passwd triggers NEED_CONFIRM
        result = await bash_command("echo data > /etc/passwd", context=context)
        assert "Safety notice" in result or "confirmation" in result

    @pytest.mark.asyncio
    async def test_need_confirm_denied_by_user(self, tmp_path: Path):
        denied = False

        async def fake_confirm(name, args):
            nonlocal denied
            denied = True
            return False

        context = AgentContext(workspace=tmp_path, confirm_callback=fake_confirm)
        result = await bash_command("echo data > /etc/passwd", context=context)
        assert denied is True
        assert "denied" in result.lower()

    @pytest.mark.asyncio
    async def test_need_confirm_approved_by_user(self, tmp_path: Path):
        approved = {"v": False}

        async def fake_confirm(name, args):
            approved["v"] = True
            return True

        context = AgentContext(workspace=tmp_path, confirm_callback=fake_confirm)
        # This will try to actually run the command after approval
        # It should not return the "needs confirmation" message
        result = await bash_command("echo data > /etc/passwd", context=context)
        assert approved["v"] is True
        assert "Safety notice" not in result
        assert "needs your confirmation" not in result


# ── workspace cwd default (bash must run in context.workspace) ─────────

_unix_only = pytest.mark.skipif(bash_tool.IS_WINDOWS, reason="uses POSIX pwd")


class TestWorkspaceCwd:
    @_unix_only
    @pytest.mark.asyncio
    async def test_bash_defaults_to_workspace(self, tmp_path: Path):
        """bash with no workdir must run inside context.workspace."""
        context = AgentContext(workspace=tmp_path)
        result = await bash_command("pwd", context=context)
        assert Path(result.strip()).resolve() == Path(context.workspace).resolve()

    @_unix_only
    @pytest.mark.asyncio
    async def test_bash_workdir_override(self, tmp_path: Path):
        """An explicit workdir still wins over the workspace default."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        context = AgentContext(workspace=workspace)
        result = await bash_command("pwd", workdir=str(other), context=context)
        assert Path(result.strip()).resolve() == other.resolve()


# ── _build_subprocess_kwargs ──────────────────────────────────────────


class TestBuildSubprocessKwargs:
    def test_no_workdir(self):
        kwargs = bash_tool._build_subprocess_kwargs()
        assert "cwd" not in kwargs

    def test_with_workdir(self):
        kwargs = bash_tool._build_subprocess_kwargs("/tmp")
        assert kwargs.get("cwd") == "/tmp"

    @_unix_only
    @pytest.mark.skipif(not bash_tool._POSIX_SHELL, reason="bash not installed")
    @pytest.mark.asyncio
    async def test_commands_run_in_bash_not_sh(self, tmp_path: Path):
        """The tool is called bash; pipefail and PIPESTATUS fail under dash."""
        context = AgentContext(workspace=tmp_path)
        result = await bash_command(
            'set -o pipefail; false | true; echo "status=$?"; false | true; echo "first=${PIPESTATUS[0]}"',
            context=context,
        )
        assert "status=1" in result
        assert "first=1" in result

    @_unix_only
    @pytest.mark.skipif(not bash_tool._POSIX_SHELL, reason="bash not installed")
    @pytest.mark.asyncio
    async def test_background_commands_run_in_bash_too(self, tmp_path: Path):
        context = AgentContext(workspace=tmp_path)
        result = await bash_command('[[ -n "$BASH_VERSION" ]] && echo in-bash', background=True, context=context)
        if "in-bash" not in result:  # still running when the tool returned: read its log
            pid = result.split("PID=", 1)[1].split()[0]
            result = await bash_tool.bash_output(pid, wait_seconds=5, context=context)
        assert "in-bash" in result

    def test_includes_process_group_flag(self):
        kwargs = bash_tool._build_subprocess_kwargs()
        if not bash_tool.IS_WINDOWS:
            assert kwargs.get("start_new_session") is True


# ── _emit_bash_output ─────────────────────────────────────────────────


class TestEmitBashOutput:
    @pytest.mark.asyncio
    async def test_noop_without_context(self):
        await bash_tool._emit_bash_output(None, "text")

    @pytest.mark.asyncio
    async def test_noop_without_event_bus(self):
        await bash_tool._emit_bash_output(SimpleNamespace(event_bus=None), "text")

    @pytest.mark.asyncio
    async def test_emits_event(self):
        events = []

        class FakeBus:
            async def emit(self, event):
                events.append(event)

        ctx = SimpleNamespace(event_bus=FakeBus())
        await bash_tool._emit_bash_output(ctx, "hello", "tc1")

        assert len(events) == 1
        assert events[0].data["text"] == "hello"
        assert events[0].data["tool_call_id"] == "tc1"


# ── _get_subprocess_env ───────────────────────────────────────────────


class TestGetSubprocessEnv:
    def test_returns_dict_with_path(self):
        env = bash_tool._get_subprocess_env()
        assert isinstance(env, dict)
        assert "PATH" in env or len(env) > 0


# ── process helpers ───────────────────────────────────────────────────


class TestProcessHelpers:
    @pytest.mark.asyncio
    async def test_check_process_alive_returns_empty_on_failure(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_create(*args, **kwargs):
            raise RuntimeError("ps missing")

        monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_shell", fake_create)

        result = await bash_tool._check_process_alive(123)

        assert result == ""

    @pytest.mark.asyncio
    async def test_run_background_reports_failed_start(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        class FakeProc:
            returncode = 1

            async def communicate(self):
                return b"", b"boom"

        async def fake_create(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_shell", fake_create)
        monkeypatch.setattr(bash_tool.tempfile, "gettempdir", lambda: str(tmp_path))

        result = await bash_tool._run_background("echo hi")

        assert "failed to start" in result
        assert "boom" in result

    @pytest.mark.asyncio
    async def test_run_background_returns_running_status(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        events = []

        class FakeProc:
            returncode = 0

            async def communicate(self):
                return b"4321\n", b""

        async def fake_create(*args, **kwargs):
            return FakeProc()

        async def fake_emit(context, text, tool_call_id=""):
            events.append((text, tool_call_id))

        monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_shell", fake_create)
        monkeypatch.setattr(bash_tool.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.setattr(bash_tool, "_emit_bash_output", fake_emit)
        monkeypatch.setattr(bash_tool, "_check_process_alive", _async_return("4321 /bin/sh -c echo hi"))
        monkeypatch.setattr(bash_tool.asyncio, "sleep", _async_noop)

        ctx = AgentContext(workspace=tmp_path)
        result = await bash_tool._run_background("echo hi", context=ctx)

        assert "process is running" in result
        assert "4321" in result
        assert events

    @pytest.mark.asyncio
    async def test_auto_background_keeps_stream_fds_independent(self, monkeypatch, tmp_path):
        stdout_finished = asyncio.Event()
        release_stderr = asyncio.Event()
        tasks = []
        log_fds = []
        real_ensure_future = asyncio.ensure_future
        real_open = os.open

        class Stdout:
            def __init__(self):
                self.sent = False

            async def read(self, size):
                if not self.sent:
                    self.sent = True
                    return b"later out"
                stdout_finished.set()
                return b""

        class Stderr:
            def __init__(self):
                self.sent = False

            async def read(self, size):
                await release_stderr.wait()
                if self.sent:
                    return b""
                self.sent = True
                return b"late error"

        def track_task(coro):
            task = real_ensure_future(coro)
            tasks.append(task)
            return task

        async def check_alive(pid):
            return ""

        def track_open(path, *args):
            fd = real_open(path, *args)
            if str(path).endswith("crabagent-bg-12345.log"):
                log_fds.append(fd)
            return fd

        monkeypatch.setattr(bash_tool.os, "open", track_open)
        monkeypatch.setattr(bash_tool.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.setattr(bash_tool.asyncio, "ensure_future", track_task)
        monkeypatch.setattr(bash_tool.asyncio, "sleep", _async_noop)
        monkeypatch.setattr(bash_tool, "_check_process_alive", check_alive)
        proc = SimpleNamespace(pid=12345, stdout=Stdout(), stderr=Stderr())
        await bash_tool._auto_background(proc, ["earlier out"], ["earlier err"], "echo", 1, None)
        await stdout_finished.wait()
        await tasks[0]

        # Reuse the closed stdout descriptor for an unrelated file.
        unrelated = tmp_path / "unrelated"
        fd = os.open(unrelated, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            assert len(log_fds) == 2 and log_fds[0] != log_fds[1]
            assert fd == log_fds[0]
            release_stderr.set()
            await asyncio.gather(*tasks)
            os.write(fd, b"safe")
        finally:
            os.close(fd)
        assert unrelated.read_bytes() == b"safe"
        assert (tmp_path / "crabagent-bg-12345.log").read_bytes() == (
            b"earlier out\n[stderr]\nearlier errlater out[stderr] late error"
        )

    @pytest.mark.asyncio
    async def test_auto_background_without_streams_does_not_open_fd(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bash_tool.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.setattr(bash_tool.asyncio, "sleep", _async_noop)
        monkeypatch.setattr(bash_tool, "_check_process_alive", _async_return(""))

        def unexpected_open(*args, **kwargs):
            raise AssertionError("no drain descriptor should be opened")

        monkeypatch.setattr(bash_tool.os, "open", unexpected_open)
        proc = SimpleNamespace(pid=12346, stdout=None, stderr=None)
        await bash_tool._auto_background(proc, [], [], "echo", 1, None)

    @pytest.mark.asyncio
    async def test_drain_to_fd_writes_prefix_once(self, tmp_path: Path):
        class FakeStream:
            def __init__(self):
                self.chunks = [b"abc", b"def", b""]

            async def read(self, size):
                return self.chunks.pop(0)

        path = tmp_path / "out.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        await bash_tool._drain_to_fd(FakeStream(), fd, prefix=b"[stderr] ")

        content = path.read_bytes()
        assert content == b"[stderr] abcdef"


# ── sandbox extra coverage ────────────────────────────────────────────


class TestSandboxExtra:
    def test_block_device_write(self):
        from crabagent.core.agent.tools.sandbox import validate_command

        assert validate_command("dd if=/dev/zero of=/dev/sda") is not None

    def test_fork_bomb_pattern(self):
        from crabagent.core.agent.tools.sandbox import validate_command

        assert validate_command(":(){ :|:& };:") is not None

    def test_safe_command_passes(self):
        from crabagent.core.agent.tools.sandbox import validate_command

        assert validate_command("echo hello") is None

    def test_background_pattern_blocked_when_enabled(self, monkeypatch):
        from crabagent.core.agent.tools import sandbox

        monkeypatch.setattr(sandbox.settings, "bash_block_background", True)
        result = sandbox.validate_command("tmux new-session")
        assert result is not None

    def test_truncate_output_with_custom_max(self):
        from crabagent.core.agent.tools.sandbox import truncate_output

        result = truncate_output("A" * 1000, max_length=100)
        assert len(result) < 300
        assert "truncated" in result


async def _async_noop(*args, **kwargs):
    return None


def _async_return(value):
    async def inner(*args, **kwargs):
        return value

    return inner


@pytest.mark.asyncio
async def test_stream_output_preserves_split_utf8_and_terminal_controls(monkeypatch):
    raw = "text🙂\r\x1b[2Kdone\n".encode("utf-8")

    class FakeStream:
        def __init__(self, chunks):
            self.chunks = iter(chunks)

        async def read(self, size):
            return next(self.chunks, b"")

    events = []

    async def capture(context, text, tool_call_id=""):
        events.append((text, tool_call_id))

    monkeypatch.setattr(bash_tool, "_ENCODING", "utf-8")
    monkeypatch.setattr(bash_tool, "_emit_bash_output", capture)
    proc = SimpleNamespace(
        stdout=FakeStream([raw[i:i + 1] for i in range(len(raw))]),
        stderr=FakeStream([]),
    )
    stdout, stderr = [], []
    await bash_tool._stream_output(proc, stdout, stderr, object(), "call-1")
    assert "".join(stdout) == raw.decode("utf-8")
    assert "".join(text for text, _ in events) == raw.decode("utf-8")
    assert all(call_id == "call-1" for _, call_id in events)


# ── background jobs: bash_output / bash_stop ──────────────────────────

unix_only = pytest.mark.skipif(bash_tool.IS_WINDOWS, reason="uses POSIX shell and signals")


def _session(tmp_path: Path, session_id: str) -> AgentContext:
    context = AgentContext(workspace=tmp_path)
    context.metadata["session_id"] = session_id
    return context


def _pid_of(result: str) -> int:
    return int(result.split("PID=")[1].split()[0])


@pytest.fixture
def background_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Keep background logs inside tmp_path and stop every job the test started."""
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(bash_tool.tempfile, "gettempdir", lambda: str(logs))
    before = set(bash_tool._JOBS)
    yield logs
    for pid in set(bash_tool._JOBS) - before:
        job = bash_tool._JOBS.pop(pid)
        try:
            os.killpg(os.getpgid(job.pid), 9)
        except (OSError, AttributeError):
            pass


class TestBackgroundJobs:
    @unix_only
    @pytest.mark.asyncio
    async def test_each_background_command_gets_its_own_log(self, tmp_path: Path, background_logs: Path):
        context = _session(tmp_path, "s1")

        first = await bash_command("echo one; sleep 30", background=True, context=context)
        second = await bash_command("echo two; sleep 30", background=True, context=context)

        job_one = bash_tool._JOBS[_pid_of(first)]
        job_two = bash_tool._JOBS[_pid_of(second)]
        assert job_one.log_file != job_two.log_file
        assert Path(job_one.log_file).read_text().strip() == "one"
        assert Path(job_two.log_file).read_text().strip() == "two"
        assert "bash_output(pid=" in first and "bash_stop(pid=" in first

    @unix_only
    @pytest.mark.asyncio
    async def test_bash_output_returns_only_new_output(self, tmp_path: Path, background_logs: Path):
        context = _session(tmp_path, "s1")
        started = await bash_command("echo first; sleep 2; echo second; sleep 30", background=True, context=context)
        pid = _pid_of(started)

        initial = await bash_tool.bash_output(pid=pid, context=context)
        later = await bash_tool.bash_output(pid=pid, wait_seconds=10, context=context)
        idle = await bash_tool.bash_output(pid=pid, context=context)

        assert "running" in initial.splitlines()[0]
        assert "first" in initial and "second" not in initial
        assert "second" in later and "first" not in later
        assert idle.splitlines()[-1] == "[no new output]"

    @unix_only
    @pytest.mark.asyncio
    async def test_finished_command_reports_exit_code_and_output(self, tmp_path: Path, background_logs: Path):
        context = _session(tmp_path, "s1")

        result = await bash_command("echo done; exit 3", background=True, context=context)

        assert "already exited (exit code 3)" in result
        assert "done" in result
        listing = await bash_tool.bash_output(context=context)
        assert f"PID {_pid_of(result)} | exited (exit code 3)" in listing

    @unix_only
    @pytest.mark.asyncio
    async def test_bash_stop_terminates_the_whole_process_tree(self, tmp_path: Path, background_logs: Path):
        context = _session(tmp_path, "s1")
        marker = tmp_path / "child.pid"
        started = await bash_command(
            f"sh -c 'echo $$ > {marker}; sleep 60' & wait", background=True, context=context
        )
        pid = _pid_of(started)
        child_pid = int(marker.read_text().strip())

        result = await bash_tool.bash_stop(pid=pid, context=context)

        assert "stopped" in result
        assert await bash_tool._check_process_alive(pid) == ""
        assert await bash_tool._check_process_alive(child_pid) == ""
        assert "nothing to stop" in await bash_tool.bash_stop(pid=pid, context=context)

    @unix_only
    @pytest.mark.asyncio
    async def test_jobs_are_private_to_the_session_that_started_them(self, tmp_path: Path, background_logs: Path):
        owner = _session(tmp_path, "owner")
        stranger = _session(tmp_path, "stranger")
        started = await bash_command("sleep 30", background=True, context=owner)
        pid = _pid_of(started)

        assert "no background command" in await bash_tool.bash_output(pid=pid, context=stranger)
        assert "no background command" in await bash_tool.bash_stop(pid=pid, context=stranger)
        assert "No background commands" in await bash_tool.bash_output(context=stranger)
        assert await bash_tool._check_process_alive(pid) != ""

    @unix_only
    @pytest.mark.asyncio
    async def test_timed_out_command_can_be_followed_and_stopped(self, tmp_path: Path, background_logs: Path):
        context = _session(tmp_path, "s1")

        result = await bash_command("echo alpha; sleep 2; echo omega; sleep 60", timeout=1000, context=context)
        pid = int(result.split("PID: ")[1].split()[0])

        assert "[auto-background]" in result and f"bash_output(pid={pid})" in result
        assert "alpha" in result
        followed = await bash_tool.bash_output(pid=pid, wait_seconds=10, context=context)
        assert "omega" in followed and "alpha" not in followed
        assert "stopped" in await bash_tool.bash_stop(pid=pid, context=context)

    @pytest.mark.asyncio
    async def test_unknown_or_invalid_pid_is_reported(self, tmp_path: Path):
        context = _session(tmp_path, "s1")

        assert "no background command" in await bash_tool.bash_output(pid=999999, context=context)
        assert "pid must be a number" in await bash_tool.bash_stop(pid="abc", context=context)

"""Tests for per-tool invocation telemetry classification (W1).

Contract under test: ``crabagent.core.agent.tool_telemetry.classify_tool_outcome(name, result)``
returns ``(ok, reason_class)`` where ``reason_class`` is one of
{ok, unknown_tool, no_session, permission_denied, user_denied, blocked, error,
exit_nonzero}.

One test per behaviour rule in the spec, plus two representative edge cases
(non-string results, user-denied confirmation).
"""

from __future__ import annotations

from crabagent.core.agent.tool_telemetry import classify_tool_outcome


def test_ok_for_plain_output():
    """A successful tool result (no 'Error' prefix) classifies as ok."""
    assert classify_tool_outcome("bash", "some output") == (True, "ok")


def test_no_session():
    """Session-gated tools failing unattended classify as no_session."""
    result = "Error: agent_runs_inspect requires an active session"

    assert classify_tool_outcome("agent_runs_inspect", result) == (False, "no_session")


def test_unknown_tool():
    """A hallucinated / unloaded tool name classifies as unknown_tool."""
    result = "Error: tool 'foobar' is not available (may be restricted for current agent)"

    assert classify_tool_outcome("foobar", result) == (False, "unknown_tool")


def test_permission_denied():
    """A permission-gated tool refused by policy classifies as permission_denied."""
    result = "Tool 'bash' is disabled (no permission for current agent)"

    assert classify_tool_outcome("bash", result) == (False, "permission_denied")


def test_error_class():
    """Any other error string falls through to the generic error class."""
    assert classify_tool_outcome("bash", "Error executing bash: boom") == (False, "error")


def test_user_denied():
    """A confirmation refused by the user classifies as user_denied.

    Mirrors the real registry message (tools/registry.py), which carries no
    ``Error:`` prefix.
    """
    assert classify_tool_outcome("bash", "Tool 'bash' execution denied by user.") == (False, "user_denied")


def test_non_string_result_is_ok():
    """Non-string results (structured payloads) are treated as successful."""
    assert classify_tool_outcome("read", {"content": "x"}) == (True, "ok")


# ── Shell commands that ran but failed ──────────────────────────────────────


def test_bash_with_a_nonzero_exit_code_is_a_failure():
    """bash ends a failed command's output with its exit code: the tool worked, the command failed."""
    result = "FAILED tests/test_x.py::test_y\n1 failed in 0.2s\n[exit code: 1]"

    assert classify_tool_outcome("bash", result) == (False, "exit_nonzero")


def test_bash_killed_by_a_signal_is_a_failure():
    assert classify_tool_outcome("bash", "partial\n[stderr]\nTerminated\n[exit code: -15]") == (False, "exit_nonzero")


def test_bash_that_succeeded_is_ok():
    """Exit code 0 prints no marker."""
    assert classify_tool_outcome("bash", "3 passed in 0.1s") == (True, "ok")


def test_bash_moved_to_the_background_is_not_a_failure():
    """An auto-backgrounded command shows exit code -1 mid-text; it is still running."""
    result = (
        "building...\n[exit code: -1]\n"
        "[auto-background] Command still running after 120s; moved to the background\n"
        "  PID: 4242\n  Follow it: bash_output(pid=4242)\n"
    )

    assert classify_tool_outcome("bash", result) == (True, "ok")


def test_bash_output_that_merely_mentions_an_exit_code_is_ok():
    """Only the marker at the very end counts, not the same text inside a printed log."""
    result = "old run:\n[exit code: 1]\nretried:\nall good"

    assert classify_tool_outcome("bash", result) == (True, "ok")


def test_background_job_that_exited_nonzero_is_a_failure():
    result = "[PID 4242] exited (exit code 2) | started 3m ago | log: deleted, all output shown\nboom"

    assert classify_tool_outcome("bash_output", result) == (False, "exit_nonzero")


def test_background_job_that_exited_cleanly_or_still_runs_is_ok():
    done = "[PID 4242] exited (exit code 0) | started 3m ago | log: deleted, all output shown\nok"
    running = "[PID 4242] running | started 1m ago | log: /tmp/crabagent-bg-4242.log\n[no new output]"

    assert classify_tool_outcome("bash_output", done) == (True, "ok")
    assert classify_tool_outcome("bash_output", running) == (True, "ok")


def test_exit_code_marker_from_another_tool_is_ignored():
    """A file that happens to end with the marker is not a failed command."""
    assert classify_tool_outcome("read", "1: notes\n2: [exit code: 1]") == (True, "ok")


def test_tool_that_raised_is_an_error():
    """The loop records a handler exception as "Tool error: ..."."""
    assert classify_tool_outcome("edit", "Tool error: boom") == (False, "error")


def test_bash_refused_by_the_user_or_the_sandbox():
    assert classify_tool_outcome("bash", "Command denied by user: writes to /etc") == (False, "user_denied")
    assert classify_tool_outcome("bash", "Command blocked: privilege escalation ('sudo')") == (False, "blocked")

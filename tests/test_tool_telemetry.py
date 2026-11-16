"""Tests for per-tool invocation telemetry classification (W1).

Contract under test: ``crabagent.core.agent.tool_telemetry.classify_tool_outcome(name, result)``
returns ``(ok, reason_class)`` where ``reason_class`` is one of
{ok, unknown_tool, no_session, permission_denied, user_denied, error, truncated}.

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

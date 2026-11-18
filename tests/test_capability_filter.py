"""``agent_switch.filter_tool_registry`` honours ``execution_mode``.

An ``"unattended"`` run must not be offered session-gated tools, because such a
run has no session/user id and every call to them fails with "requires an
active session". The mode check must win over the always-keep branch used for
memory tools. The default ``"interactive"`` mode keeps today's behaviour.
"""

from __future__ import annotations

from crabagent.core.agent.agent_switch import filter_tool_registry
from crabagent.core.agent.tools.registry import ToolRegistry

PARAMS = {"type": "object", "properties": {}}


def _registry(names: list[str]) -> ToolRegistry:
    reg = ToolRegistry()
    for name in names:
        reg.register(name, f"{name} tool", PARAMS)(lambda **kwargs: None)
    return reg


def test_filter_unattended_drops_session_gated():
    """An unattended run must not be offered session-gated tools."""
    source = _registry(["bash", "git_diff", "shared_put"])

    filtered = filter_tool_registry(source, execution_mode="unattended")

    names = set(filtered._tools)
    assert "bash" in names
    assert "git_diff" not in names
    assert "shared_put" not in names
    # the source pool is never mutated
    assert set(source._tools) == {"bash", "git_diff", "shared_put"}


def test_filter_interactive_keeps_session_gated():
    """Default interactive mode is unchanged: nothing mode-dropped."""
    source = _registry(["bash", "git_diff", "shared_put"])

    filtered = filter_tool_registry(source)

    names = set(filtered._tools)
    assert "bash" in names
    assert "git_diff" in names
    assert "shared_put" in names



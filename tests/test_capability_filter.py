"""Contract tests for W3: capability-manifest -> load-time filter wiring.

Two behaviours must hold once W3 lands:

* ``agent_switch.filter_tool_registry`` honours ``execution_mode``: an
  ``"unattended"`` run must not be offered session-gated tools, because such a
  run has no session/user id and every call to them fails with "requires an
  active session". The mode check must win over the always-keep branch used for
  memory tools. The default ``"interactive"`` mode keeps today's behaviour.
* ``capability_manifest.default_manifest()`` derives ``core`` from the live
  global registry (every registered tool that belongs to no deferred pack)
  instead of returning a hardcoded list, so newly registered always-on tools
  show up without a code change.
"""

from __future__ import annotations

from crabagent.core.agent.agent_switch import filter_tool_registry
from crabagent.core.agent.capability_manifest import default_manifest
from crabagent.core.agent.tools.registry import ToolRegistry, registry
from crabagent.core.agent.tools.tool_search import _GROUP_OF_TOOL

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


def test_default_manifest_core_tracks_registry():
    """core must come from the live registry, not a frozen literal."""
    probe = "_w3_core_probe_tool"
    assert probe not in _GROUP_OF_TOOL, "probe must not belong to any deferred pack"
    assert probe not in registry._tools

    registry.register(probe, "w3 core probe", PARAMS)(lambda **kwargs: None)
    try:
        core = default_manifest().core
    finally:
        registry._tools.pop(probe, None)

    assert probe in core

    # A real registered tool that is in no pack is still part of core.
    t0 = next(name for name in registry._tools if name not in _GROUP_OF_TOOL)
    assert t0 in core

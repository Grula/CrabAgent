"""Contract tests for execution-mode tool availability (atomic task W4).

These tests pin the *availability* layer only: which tools are reported as
unavailable per execution mode, the preamble text that names them, the
union used to omit them from the model request, and the ``AgentContext``
field that carries the mode. No registry wiring, no prompt injection.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from crabagent.core.agent.capability_manifest import (
    capability_preamble,
    excluded_tool_names,
    unavailable_tools,
)
from crabagent.core.agent.context import AgentContext


class _FakeRegistry:
    """Minimal stand-in exposing the registry surface the contract uses."""

    def __init__(self, names: list[str]) -> None:
        self._tools = [SimpleNamespace(name=n) for n in names]

    def list_tools(self) -> list[SimpleNamespace]:
        return self._tools


def _registry(*names: str) -> _FakeRegistry:
    return _FakeRegistry(list(names))


def test_unavailable_tools_unattended():
    """Only session-gated tools are reported, sorted by name, with a reason."""
    reg = _registry("bash", "memory_save", "agent_runs_inspect")
    assert unavailable_tools(reg, "unattended") == [
        {"name": "agent_runs_inspect", "reason": "requires an active session"},
        {"name": "memory_save", "reason": "requires an active session"},
    ]


def test_unavailable_tools_interactive_empty():
    """Interactive runs keep every tool, so nothing is unavailable."""
    reg = _registry("bash", "memory_save", "agent_runs_inspect")
    assert unavailable_tools(reg, "interactive") == []


def test_capability_preamble_empty():
    """No unavailable tools means no preamble at all (no stray whitespace)."""
    assert capability_preamble("interactive", []) == ""


def test_capability_preamble_names_reason():
    """The preamble names each unavailable tool together with its reason."""
    s = capability_preamble(
        "unattended", [{"name": "memory_save", "reason": "requires an active session"}]
    )
    assert "memory_save" in s
    assert "requires an active session" in s


def test_agent_context_default_mode():
    """AgentContext defaults to interactive; unattended is opt-in."""
    ctx = AgentContext(workspace=Path("."))
    assert ctx.execution_mode == "interactive"


def test_excluded_tool_names_union(monkeypatch):
    """Deferred pack tools and unavailable session-gated tools are unioned."""
    monkeypatch.setattr(
        "crabagent.core.agent.tools.tool_search.deferred_exclusions",
        lambda c: {"deferred_x"},
    )
    # Tolerate either import style inside excluded_tool_names: if the name was
    # bound at module import time, patch that binding too.
    monkeypatch.setattr(
        "crabagent.core.agent.capability_manifest.deferred_exclusions",
        lambda c: {"deferred_x"},
        raising=False,
    )
    ctx = SimpleNamespace(
        tool_registry=_registry("bash", "memory_save"), execution_mode="unattended"
    )
    assert excluded_tool_names(ctx) == {"deferred_x", "memory_save"}

"""Contract tests for W5: failure-modes injection into the runtime prompt.

``build_runtime_system_prompt(context)`` is the single composition point for
the system prompt handed to the model at run time. It must combine three
things, in this order:

1. the base ``context.system_prompt`` (unchanged content, still present),
2. the data-derived "Known failure modes to avoid:" block, and
3. the execution-mode capability preamble (only when tools are unavailable).

These tests pin that composition only - not the wording of the individual
blocks (those are covered by tests/test_failure_modes.py and
tests/test_capability_availability.py).
"""

from __future__ import annotations

from types import SimpleNamespace

from crabagent.core.agent.capability_manifest import build_runtime_system_prompt

FAILURE_MODES_HEADING = "Known failure modes to avoid:"
PREAMBLE_HEADING = "Unavailable tools"


class _FakeRegistry:
    """Minimal stand-in exposing the registry surface the contract uses."""

    def __init__(self, names: list[str]) -> None:
        self._tools = [SimpleNamespace(name=n) for n in names]

    def list_tools(self) -> list[SimpleNamespace]:
        return self._tools


def _ctx(system_prompt: str, execution_mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        system_prompt=system_prompt,
        execution_mode=execution_mode,
        tool_registry=_FakeRegistry(["bash", "memory_save"]),
    )


def test_build_runtime_system_prompt_includes_failure_modes():
    """Interactive runs get the failure-modes block but no tool preamble."""
    out = build_runtime_system_prompt(_ctx("BASE", "interactive"))
    assert FAILURE_MODES_HEADING in out
    assert "BASE" in out
    assert PREAMBLE_HEADING not in out


def test_build_runtime_system_prompt_unattended():
    """Unattended runs get failure modes *and* the capability preamble."""
    out = build_runtime_system_prompt(_ctx("BASE", "unattended"))
    assert FAILURE_MODES_HEADING in out
    assert PREAMBLE_HEADING in out
    assert "memory_save" in out


def test_build_runtime_system_prompt_empty():
    """An empty base prompt stays empty: nothing is injected out of nowhere."""
    assert build_runtime_system_prompt(_ctx("", "unattended")) == ""

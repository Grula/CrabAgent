"""Tests for the workmode self-inspection tool (atomic task W6).

Contract under test:
- ``crabagent.core.agent.capability_manifest.build_inspection_report(
      active_tools, unavailable, token_estimate, used_tools)`` is a pure function
  returning ``{"active_tools", "unavailable", "token_estimate", "dead_config"}``
  where ``dead_config`` is the sorted list of active tools that were never used.
- ``workmode_inspect(context)`` is an async tool returning a JSON string with the
  same four keys, sourced from the context's tool surface plus 30-day telemetry,
  and it never reports itself as dead config.

Only the report shape and the JSON tool surface are pinned here: no DB schema,
no prompt wiring.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from crabagent.core.agent.capability_manifest import (
    build_inspection_report,
    workmode_inspect,
)


class _FakeRegistry:
    """Minimal stand-in exposing the registry surface the contract uses."""

    def __init__(self, names: list[str]) -> None:
        self._tools = [SimpleNamespace(name=n) for n in names]

    def list_tools(self) -> list[SimpleNamespace]:
        return self._tools


class _FakeSession:
    """Async context manager standing in for an ``AsyncSession``."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False


def test_build_inspection_report_keys_and_dead():
    """Unused active tools land in dead_config, sorted; used ones do not."""
    report = build_inspection_report(["bash", "memory_save"], [], 123, {"bash"})
    assert report == {
        "active_tools": ["bash", "memory_save"],
        "unavailable": [],
        "token_estimate": 123,
        "dead_config": ["memory_save"],
    }


def test_build_inspection_report_empty():
    """An empty tool surface reports empty lists, not None or a placeholder."""
    assert build_inspection_report([], [], 0, set()) == {
        "active_tools": [],
        "unavailable": [],
        "token_estimate": 0,
        "dead_config": [],
    }


async def test_workmode_inspect_returns_json(monkeypatch):
    """The tool returns JSON with the four report keys and correct values."""

    async def _fake_aggregate(db, since_days=30):
        return [
            {
                "tool_name": "bash",
                "calls": 1,
                "failures": 0,
                "failure_rate": 0.0,
                "last_used": None,
                "avg_latency_ms": 10.0,
            }
        ]

    # Function-local imports inside workmode_inspect resolve against these
    # modules at call time; also cover a module-level binding if one is used.
    monkeypatch.setattr(
        "crabagent.core.database.async_session_factory", lambda: _FakeSession()
    )
    monkeypatch.setattr(
        "crabagent.core.agent.tool_telemetry.aggregate_tool_usage", _fake_aggregate
    )
    monkeypatch.setattr(
        "crabagent.core.agent.capability_manifest.async_session_factory",
        lambda: _FakeSession(),
        raising=False,
    )
    monkeypatch.setattr(
        "crabagent.core.agent.capability_manifest.aggregate_tool_usage",
        _fake_aggregate,
        raising=False,
    )

    ctx = SimpleNamespace(
        tool_registry=_FakeRegistry(["bash", "memory_save"]),
        execution_mode="interactive",
        accumulated_total=42,
    )

    out = await workmode_inspect(ctx)
    report = json.loads(out)

    assert set(report) == {
        "active_tools",
        "unavailable",
        "token_estimate",
        "dead_config",
    }
    assert report["active_tools"] == ["bash", "memory_save"]
    assert report["token_estimate"] == 42
    # bash was used (telemetry), memory_save was not -> only memory_save is dead.
    assert report["dead_config"] == ["memory_save"]
    assert "workmode_inspect" not in report["dead_config"]

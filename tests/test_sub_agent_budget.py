"""Budget-exhaustion signalling for delegated sub-agents.

Spec (atomic task): when a delegated sub-agent run stops because its iteration
budget is exhausted, ``spawn_sub_agent`` must surface that fact instead of
pretending the run was a clean success:

1. the ``SUB_AGENT_END`` event carries ``budget_exhausted == True``;
2. the queued parent message JSON contains ``"budget_exhausted": true``.

A run that finishes below ``max_iterations`` must report ``False`` (or omit the
field). Partial assistant output produced before exhaustion is still returned.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from crabagent.core.agent import agents as agents_module
from crabagent.core.agent.context import AgentContext
from crabagent.core.event import EventType

AGENT_DEF = {
    "name": "implementer",
    "display_name": "Senior Developer",
    "role": "r",
    "goal": "g",
    "backstory": "b",
    "tool_permissions": {},
}


def _async_return(value: Any):
    async def inner(*args, **kwargs):
        return value

    return inner


def _make_parent_context() -> AgentContext:
    agents_module._running_sub_agents.clear()
    return AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0})


def _capture_events(context: AgentContext) -> list:
    """Collect every event emitted on the parent bus until the test ends."""
    events: list = []

    async def _listener(event):
        events.append(event)

    context.event_bus.subscribe(_listener)
    return events


def _stub_offline_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep delegation off the DB / network for the parts that still call out."""
    monkeypatch.setattr(agents_module, "get_agent", _async_return(AGENT_DEF))
    monkeypatch.setattr(agents_module, "_load_shared_context", _async_return(""))
    monkeypatch.setattr(agents_module, "load_sub_agent_model_map", _async_return({}))


def _install_run_agent_stub(
    monkeypatch: pytest.MonkeyPatch, *, exhaust_budget: bool
) -> list[str]:
    """Replace the agent loop with a stub that produces partial output.

    Returns the list of tasks the stub was invoked with, so a test can prove the
    stub actually ran instead of passing vacuously.
    """
    invocations: list[str] = []

    async def stub_run_agent(sub_context, task, *args, **kwargs):
        invocations.append(task)
        sub_context.messages.append({"role": "assistant", "content": "partial done"})
        sub_context.iteration = (
            sub_context.max_iterations
            if exhaust_budget
            else max(0, sub_context.max_iterations - 1)
        )
        return sub_context.messages

    # Delegation is expected to resolve ``run_agent`` from this module so it can be
    # swapped without patching the loop. raising=False lets the stub be installed
    # while the old function-local import is still in place; the loop patch keeps
    # that path offline too.
    monkeypatch.setattr(agents_module, "run_agent", stub_run_agent, raising=False)
    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", stub_run_agent)
    return invocations


def _end_event_data(events: list) -> dict:
    end_events = [e for e in events if e.type == EventType.SUB_AGENT_END]
    assert len(end_events) == 1, (
        f"expected exactly one SUB_AGENT_END, got "
        f"{[str(e.type) for e in events]}"
    )
    return end_events[0].data


class TestSubAgentBudgetExhausted:
    @pytest.mark.asyncio
    async def test_exhausted_budget_is_reported_on_end_event(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _stub_offline_runtime(monkeypatch)
        invocations = _install_run_agent_stub(monkeypatch, exhaust_budget=True)
        parent = _make_parent_context()
        events = _capture_events(parent)

        result = await agents_module.spawn_sub_agent(
            parent,
            agent_name="implementer",
            task="x",
            agent_def=AGENT_DEF,
        )

        assert invocations, "run_agent stub was never invoked"
        # Partial work must survive, not be replaced by an error string.
        assert isinstance(result, str)
        assert "partial done" in result

        data = _end_event_data(events)
        assert data.get("budget_exhausted") is True

    @pytest.mark.asyncio
    async def test_exhausted_budget_is_reported_in_pending_message_json(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _stub_offline_runtime(monkeypatch)
        _install_run_agent_stub(monkeypatch, exhaust_budget=True)
        parent = _make_parent_context()
        _capture_events(parent)

        await agents_module.spawn_sub_agent(
            parent,
            agent_name="implementer",
            task="x",
            agent_def=AGENT_DEF,
        )

        pending = parent.metadata["_pending_sub_agent_messages"]
        assert pending[-1]["role"] == "sub_agent"
        content = pending[-1]["content"]
        assert '"budget_exhausted": true' in content
        assert json.loads(content)["budget_exhausted"] is True

    @pytest.mark.asyncio
    async def test_normal_completion_does_not_report_budget_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _stub_offline_runtime(monkeypatch)
        _install_run_agent_stub(monkeypatch, exhaust_budget=False)
        parent = _make_parent_context()
        events = _capture_events(parent)

        await agents_module.spawn_sub_agent(
            parent,
            agent_name="implementer",
            task="x",
            agent_def=AGENT_DEF,
        )

        data = _end_event_data(events)
        assert data.get("budget_exhausted", False) is False

        content = parent.metadata["_pending_sub_agent_messages"][-1]["content"]
        assert json.loads(content).get("budget_exhausted", False) is False

"""Tests for per-tool invocation telemetry persistence (W1b).

Contract under test:
- ``crabagent.core.database.ToolInvocation`` -> table ``tool_invocations``
- ``crabagent.core.agent.tool_telemetry.record_tool_invocations(db, entries)``
- ``crabagent.core.agent.tool_telemetry.aggregate_tool_usage(db, since_days=30)``
- ``RunRecorder`` stamps ``ok``/``reason_class`` on each buffered tool entry and
  accepts a ``session_mode`` keyword (default ``"interactive"``).

One test per behaviour rule: round-trip write, aggregation, empty aggregation,
and outcome classification during the run.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core.agent.run_recorder import RunRecorder
from crabagent.core.agent.tool_telemetry import aggregate_tool_usage, record_tool_invocations
from crabagent.core.database import Base, ToolInvocation
from crabagent.core.event import AgentEvent, EventType


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_record_tool_invocations_roundtrip(db):
    """record_tool_invocations persists one row per entry, fields intact."""
    await record_tool_invocations(
        db,
        [
            {
                "user_id": 1,
                "session_id": "s1",
                "run_id": 10,
                "agent_name": "main",
                "tool_name": "bash",
                "ok": True,
                "reason_class": "ok",
                "latency_ms": 120,
                "session_mode": "interactive",
            },
            {
                "user_id": 2,
                "session_id": "s2",
                "run_id": 11,
                "agent_name": "coder",
                "tool_name": "agent_runs_inspect",
                "ok": False,
                "reason_class": "no_session",
                "latency_ms": 3,
                "session_mode": "unattended",
            },
        ],
    )

    rows = (await db.execute(select(ToolInvocation).order_by(ToolInvocation.id))).scalars().all()

    assert len(rows) == 2
    assert [r.tool_name for r in rows] == ["bash", "agent_runs_inspect"]
    assert [r.ok for r in rows] == [True, False]
    assert [r.reason_class for r in rows] == ["ok", "no_session"]
    assert [r.latency_ms for r in rows] == [120, 3]
    assert [r.session_mode for r in rows] == ["interactive", "unattended"]


async def test_aggregate_tool_usage(db):
    """Aggregation groups by tool_name with failures, rate, latency and ordering."""
    db.add_all(
        [
            # bash: 3 calls, 1 failure, latencies 10/20/30 -> mean 20.0
            ToolInvocation(tool_name="bash", ok=True, reason_class="ok", latency_ms=10),
            ToolInvocation(tool_name="bash", ok=True, reason_class="ok", latency_ms=20),
            ToolInvocation(tool_name="bash", ok=False, reason_class="error", latency_ms=30),
            ToolInvocation(tool_name="grep", ok=True, reason_class="ok", latency_ms=5),
        ]
    )
    await db.commit()

    stats = await aggregate_tool_usage(db, since_days=30)

    assert [s["tool_name"] for s in stats] == ["bash", "grep"]
    bash, grep = stats[0], stats[1]
    assert bash["calls"] == 3
    assert bash["failures"] == 1
    assert bash["failure_rate"] == 0.333
    assert bash["avg_latency_ms"] == 20.0
    assert bash["last_used"] is not None
    assert grep["calls"] == 1
    assert grep["failures"] == 0
    assert grep["failure_rate"] == 0.0


async def test_aggregate_tool_usage_empty(db):
    """An empty table aggregates to an empty list, not None or a placeholder row."""
    assert await aggregate_tool_usage(db) == []


async def test_run_recorder_classifies_outcome(monkeypatch: pytest.MonkeyPatch):
    """_on_tool_result stamps ok/reason_class; session_mode is configurable."""
    recorder = RunRecorder(user_id=1, session_id="s1", model="gpt-4o", session_mode="unattended")
    assert recorder._session_mode == "unattended"
    recorder._main_run_id = 1

    # A session-gated tool failing unattended.
    recorder._main_tool_buf = [
        {"name": "agent_runs_inspect", "args": {}, "started_at": 0, "result_summary": None, "elapsed": 0}
    ]
    await recorder.on_event(
        AgentEvent(
            type=EventType.TOOL_RESULT,
            data={
                "name": "agent_runs_inspect",
                "result": "Error: agent_runs_inspect requires an active session",
                "id": "tc1",
            },
        )
    )
    entry = recorder._main_tool_buf[0]
    assert entry.get("ok") is False, entry
    assert entry.get("reason_class") == "no_session", entry

    # A successful tool.
    recorder._main_tool_buf.append(
        {"name": "bash", "args": {}, "started_at": 0, "result_summary": None, "elapsed": 0}
    )
    await recorder.on_event(
        AgentEvent(type=EventType.TOOL_RESULT, data={"name": "bash", "result": "some output", "id": "tc2"})
    )
    entry = recorder._main_tool_buf[1]
    assert entry.get("ok") is True, entry
    assert entry.get("reason_class") == "ok", entry


async def test_run_recorder_keeps_the_loops_verdict_over_the_preview():
    """The event's result is a front-cut preview; the loop already classified the full text."""
    recorder = RunRecorder(user_id=1, session_id="s1", model="gpt-4o")
    recorder._main_run_id = 1
    recorder._main_tool_buf = [{"name": "bash", "args": {}, "started_at": 0, "result_summary": None, "elapsed": 0}]

    await recorder.on_event(
        AgentEvent(
            type=EventType.TOOL_RESULT,
            data={"name": "bash", "result": "F" * 2000, "id": "tc1", "ok": False, "reason_class": "exit_nonzero"},
        )
    )

    entry = recorder._main_tool_buf[0]
    assert (entry["ok"], entry["reason_class"]) == (False, "exit_nonzero")


async def test_sub_agent_tool_results_carry_the_verdict_to_the_parent(monkeypatch: pytest.MonkeyPatch):
    """A sub-agent's results reach the parent cut to 500 chars, so the verdict travels with them."""
    from pathlib import Path

    from crabagent.core.agent import agents as agents_module
    from crabagent.core.agent.context import AgentContext

    agent_def = {
        "name": "tester", "display_name": "Tester", "role": "r", "goal": "g", "backstory": "b",
        "tool_permissions": {},
    }

    async def _ret(value):
        return value

    monkeypatch.setattr(agents_module, "get_agent", lambda *a, **k: _ret(agent_def))
    monkeypatch.setattr(agents_module, "_load_shared_context", lambda *a, **k: _ret(""))
    monkeypatch.setattr(agents_module, "load_sub_agent_model_map", lambda *a, **k: _ret({}))

    async def stub_run_agent(sub_context, task, *args, **kwargs):
        await sub_context.event_bus.emit(AgentEvent(
            type=EventType.TOOL_RESULT,
            data={"name": "bash", "result": "F" * 2000, "id": "tc1", "ok": False, "reason_class": "exit_nonzero"},
        ))
        sub_context.messages.append({"role": "assistant", "content": "done"})
        return sub_context.messages

    monkeypatch.setattr(agents_module, "run_agent", stub_run_agent, raising=False)
    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", stub_run_agent)

    agents_module._running_sub_agents.clear()
    parent = AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0})
    events: list = []

    async def _listen(event):
        events.append(event)

    parent.event_bus.subscribe(_listen)

    await agents_module.spawn_sub_agent(parent, agent_name="tester", task="x", agent_def=agent_def)

    forwarded = [e for e in events if e.type == EventType.SUB_AGENT_TOOL_RESULT]
    assert len(forwarded) == 1
    assert len(forwarded[0].data["result"]) == 500
    assert (forwarded[0].data["ok"], forwarded[0].data["reason_class"]) == (False, "exit_nonzero")


async def test_a_successful_read_that_quotes_the_trap_phrases_is_a_success():
    """2026-11-26 audit item 28: only a result that starts with "Error" is a failure.

    Reading CrabAgent's own source returns lines containing "requires an active
    session" and "is not available (may be restricted"; those are content, not a
    verdict, and used to be counted as failures.
    """
    from crabagent.core.agent.tool_telemetry import classify_tool_outcome

    result = (
        "   33:     if 'requires an active session' in s or 'no user_id in context' in s:\n"
        "   35:     if 'is not available (may be restricted' in s:\n"
        "   37:     if 'is disabled (no permission' in s:\n"
    )

    assert classify_tool_outcome("read", result) == (True, "ok")


async def test_a_registry_error_still_classifies_as_a_failure():
    """A real "Error: ..." result keeps its failure class and the stored shape."""
    from crabagent.core.agent.tool_telemetry import classify_tool_outcome

    assert classify_tool_outcome(
        "agent_runs_inspect", "Error: agent_runs_inspect requires an active session"
    ) == (False, "no_session")
    assert classify_tool_outcome("bash", "Error executing bash: boom") == (False, "error")

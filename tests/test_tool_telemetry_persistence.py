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

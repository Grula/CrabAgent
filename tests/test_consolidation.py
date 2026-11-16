"""Tests for the tool consolidation replay/verification harness (W8).

Contract under test (``crabagent.core.agent.consolidation``):

- ``verify_consolidation(transcripts, available_tools, replacements=None)``
  returns a list of ``{"transcript": index, "tool": name}`` violations, in order.
  Only *successful* transcripts must be preserved, and a tool with a documented
  replacement counts as preserved.
- ``load_transcripts_from_runs(db, limit=100)`` turns completed ``AgentRun``
  rows into ``{"tool_calls": [names...], "success": True}`` transcripts.

This harness compares task success, not transcript equality: it flags a
consolidation that would break a previously-successful tool call.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core.agent.consolidation import load_transcripts_from_runs, verify_consolidation
from crabagent.core.database import AgentRun, Base


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _tool_call(name: str) -> dict:
    """A single entry in the RunRecorder ``tool_calls`` JSON format."""
    return {"name": name, "args": {}, "started_at": 0, "result_summary": "", "elapsed": 0}


def test_verify_removed_tool_flagged():
    """A tool that is gone and has no replacement is a violation."""
    transcripts = [{"tool_calls": ["bash", "read"], "success": True}]

    assert verify_consolidation(transcripts, {"bash"}) == [{"transcript": 0, "tool": "read"}]


def test_verify_replacement_not_flagged():
    """A removed tool with a documented replacement preserves the task."""
    transcripts = [{"tool_calls": ["read"], "success": True}]

    assert verify_consolidation(transcripts, {"bash"}, {"read": "edit"}) == []


def test_verify_ignores_failed_transcript():
    """Failed transcripts are skipped: only successful tasks must be preserved."""
    transcripts = [{"tool_calls": ["read"], "success": False}]

    assert verify_consolidation(transcripts, set()) == []


async def test_load_transcripts_from_runs(db):
    """Only completed runs become transcripts; tool names come from tool_calls."""
    db.add_all(
        [
            AgentRun(
                user_id=1,
                agent_name="main",
                status="completed",
                tool_calls=[_tool_call("bash"), _tool_call("read")],
            ),
            AgentRun(
                user_id=1,
                agent_name="main",
                status="failed",
                tool_calls=[_tool_call("write")],
            ),
        ]
    )
    await db.commit()

    transcripts = await load_transcripts_from_runs(db)

    assert len(transcripts) == 1
    assert transcripts[0] == {"tool_calls": ["bash", "read"], "success": True}

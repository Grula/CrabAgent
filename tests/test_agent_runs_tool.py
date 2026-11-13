"""Tests for the read-only ``agent_runs_inspect`` builtin tool.

The tool lives in ``src/crabagent/core/agent/tools/agent_runs.py`` and must:
- register itself as ``agent_runs_inspect`` in the global tool registry,
- SELECT (read-only) from the ``agent_runs`` table for the current user,
- render a markdown table plus an aggregate header,
- cap ``limit`` at 100,
- return an error string (never raise) when no ``user_id`` is available.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.registry import registry

_MODULE = "crabagent.core.agent.tools.agent_runs"

# Long enough to be truncated to 80 chars by the tool.
_LONG_SUMMARY = (
    "Investigate the streaming parser regression that only reproduces on windows "
    "when the codepage switches mid-stream and the decoder keeps buffered bytes"
)


def _tool_module():
    """Import the tool module (registration happens as an import side effect)."""
    return importlib.import_module(_MODULE)


def _tool():
    module = _tool_module()
    return getattr(module, "agent_runs_inspect")


def _context(user_id=7) -> AgentContext:
    context = AgentContext(workspace=Path.cwd())
    context.metadata.update(user_id=user_id, session_id="session-1", branch_id="main")
    return context


def _patch_session_factory(monkeypatch, factory) -> None:
    """Point both the database module and the tool module at the test database."""
    monkeypatch.setattr(db, "async_session_factory", factory)
    try:
        module = _tool_module()
    except ModuleNotFoundError:
        return
    if getattr(module, "async_session_factory", None) is not None:
        monkeypatch.setattr(module, "async_session_factory", factory)


@pytest.fixture
async def runs_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    _patch_session_factory(monkeypatch, factory)
    yield factory
    await engine.dispose()


async def _seed(factory, rows: list[dict]) -> None:
    async with factory() as session:
        for row in rows:
            session.add(db.AgentRun(**row))
        await session.commit()


def _non_table_text(output: str) -> str:
    """Aggregate header lives outside the markdown table; search it first."""
    lines = [line for line in output.splitlines() if "|" not in line]
    text = "\n".join(lines)
    return text or output


def _table_header(output: str) -> str:
    for line in output.splitlines():
        if "|" in line:
            return line.lower()
    return ""


@pytest.mark.asyncio
async def test_tool_is_registered():
    _tool_module()
    info = registry.get("agent_runs_inspect")
    assert info is not None
    assert info.handler is _tool()


@pytest.mark.asyncio
async def test_returns_rows_and_aggregate_header(runs_db):
    await _seed(
        runs_db,
        [
            {
                "user_id": 7,
                "session_id": "session-1",
                "agent_name": "coder",
                "status": "completed",
                "started_at": 1_700_000_001.0,
                "elapsed": 10.0,
                "iterations": 5,
                "tokens_used": 4321,
                "task_summary": "Refactor the streaming parser",
            },
            {
                "user_id": 7,
                "session_id": "session-1",
                "agent_name": "coder",
                "status": "failed",
                "started_at": 1_700_000_002.0,
                "elapsed": 20.0,
                "iterations": 15,
                "tokens_used": 2000,
                "task_summary": "Fix flaky resume test",
                "error": "Traceback: boom",
            },
            {
                "user_id": 7,
                "session_id": "session-1",
                "agent_name": "researcher",
                "status": "completed",
                "started_at": 1_700_000_003.0,
                "elapsed": 30.0,
                "iterations": 10,
                "tokens_used": 3000,
                "task_summary": _LONG_SUMMARY,
            },
            {
                # Another user's run must never show up in this user's report.
                "user_id": 99,
                "session_id": "session-99",
                "agent_name": "coder",
                "status": "completed",
                "started_at": 1_700_000_004.0,
                "elapsed": 99.0,
                "iterations": 99,
                "tokens_used": 9999,
                "task_summary": "SHOULD-NOT-LEAK",
            },
        ],
    )

    result = await _tool()(context=_context())

    assert isinstance(result, str)
    # Markdown table with the required columns.
    header = _table_header(result)
    assert header, result
    for column in (
        "id",
        "started_at",
        "agent_name",
        "status",
        "elapsed",
        "iterations",
        "tokens_used",
        "error",
        "task_summary",
    ):
        assert column in header, f"missing column {column!r} in {header!r}"

    # Rows for the current user only.
    assert "Refactor the streaming parser" in result
    assert "Fix flaky resume test" in result
    assert "researcher" in result
    assert "Traceback: boom" in result
    assert "4321" in result
    assert "SHOULD-NOT-LEAK" not in result

    # Task summary is truncated to 80 characters (ellipsis style accepted).
    assert _LONG_SUMMARY[:80] in result or _LONG_SUMMARY[:79] in result
    assert _LONG_SUMMARY not in result

    # Aggregate header: total matched, counts by status, medians.
    aggregate = _non_table_text(result)
    assert re.search(r"total\D{0,20}3(?!\d)", aggregate, re.IGNORECASE), aggregate
    assert re.search(r"completed\D{0,10}2(?!\d)", aggregate, re.IGNORECASE), aggregate
    assert re.search(r"failed\D{0,10}1(?!\d)", aggregate, re.IGNORECASE), aggregate
    assert re.search(r"median\D{0,40}20(?:\.0+)?(?!\d)", aggregate, re.IGNORECASE), aggregate
    assert re.search(r"median\D{0,40}10(?:\.0+)?(?!\d)", aggregate, re.IGNORECASE), aggregate


@pytest.mark.asyncio
async def test_filters_by_agent_name_and_status(runs_db):
    await _seed(
        runs_db,
        [
            {
                "user_id": 7,
                "agent_name": "coder",
                "status": "completed",
                "started_at": 1_700_000_001.0,
                "elapsed": 10.0,
                "iterations": 5,
                "task_summary": "Refactor the streaming parser",
            },
            {
                "user_id": 7,
                "agent_name": "coder",
                "status": "failed",
                "started_at": 1_700_000_002.0,
                "elapsed": 20.0,
                "iterations": 15,
                "task_summary": "Fix flaky resume test",
                "error": "Traceback: boom",
            },
            {
                "user_id": 7,
                "agent_name": "researcher",
                "status": "completed",
                "started_at": 1_700_000_003.0,
                "elapsed": 30.0,
                "iterations": 10,
                "task_summary": "Survey parser libraries",
            },
        ],
    )

    result = await _tool()(agent_name="coder", status="failed", context=_context())

    assert "Fix flaky resume test" in result
    assert "Refactor the streaming parser" not in result
    assert "Survey parser libraries" not in result
    assert "researcher" not in result

    aggregate = _non_table_text(result)
    assert re.search(r"total\D{0,20}1(?!\d)", aggregate, re.IGNORECASE), aggregate


@pytest.mark.asyncio
async def test_limit_is_capped_at_100(runs_db):
    await _seed(
        runs_db,
        [
            {
                "user_id": 7,
                "agent_name": "coder",
                "status": "completed",
                "started_at": 1_700_000_000.0 + index,
                "elapsed": 1.0,
                "iterations": 1,
                "tokens_used": 10,
                "task_summary": f"m{index:03d}",
            }
            for index in range(120)
        ],
    )

    result = await _tool()(limit=1000, context=_context())

    markers = set(re.findall(r"m(\d{3})", result))
    assert len(markers) == 100, f"expected the 100-row cap, saw {len(markers)} rows"
    assert len(markers) < 120


@pytest.mark.asyncio
async def test_missing_user_id_returns_error_string(runs_db):
    result = await _tool()(context=None)
    assert isinstance(result, str)
    assert "error" in result.lower()

    result = await _tool()(context=_context(user_id=0))
    assert isinstance(result, str)
    assert "error" in result.lower()

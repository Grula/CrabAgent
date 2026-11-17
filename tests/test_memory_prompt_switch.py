"""Saved memories in the main prompt: off by default, and working when switched on."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db
from crabagent.core.agent import agents
from crabagent.core.config import settings


@pytest.fixture
async def memory_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)
    async with factory() as session:
        session.add_all([
            db.AgentMemory(user_id=1, memory_type="team", agent_name="", category="architecture", key="global-note",
                           content="Everyone uses FastAPI.", importance=0.9, scope="global", recall_policy="always"),
            db.AgentMemory(user_id=1, memory_type="team", agent_name="", category="architecture", key="proj-a",
                           content="Project A uses NSQ.", importance=0.9, scope="workspace",
                           workspace_path="/work/a", recall_policy="always"),
            db.AgentMemory(user_id=1, memory_type="team", agent_name="", category="architecture", key="proj-b",
                           content="Project B uses Kafka.", importance=0.9, scope="workspace",
                           workspace_path="/work/b", recall_policy="always"),
        ])
        await session.commit()
    yield factory
    await engine.dispose()


async def test_off_by_default(memory_db):
    assert settings.memory_prompt_enabled is False
    assert await agents.build_memory_prompt(1, workspace_path="/work/a") == ""


async def test_switched_on_it_works_against_a_real_database(memory_db, monkeypatch: pytest.MonkeyPatch):
    # The old call passed scope/recall filters the query function didn't accept,
    # so this raised TypeError (swallowed upstream) and nothing was ever injected.
    monkeypatch.setattr(settings, "memory_prompt_enabled", True)

    prompt = await agents.build_memory_prompt(1, workspace_path="/work/a")

    assert "Everyone uses FastAPI." in prompt
    assert "Project A uses NSQ." in prompt
    assert "Kafka" not in prompt  # another project's note stays out


async def test_query_filters(memory_db):
    rows = await db.agent_memory_get_by_type(1, "team", scope="workspace", workspace_path="/work/b")
    assert [r["key"] for r in rows] == ["proj-b"]
    assert len(await db.agent_memory_get_by_type(1, "team")) == 3

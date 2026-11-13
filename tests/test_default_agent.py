"""Default agent for new chats, and built-in agents that stay deleted."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import crabagent.core.agent.agents as agents
import crabagent.core.database as db


@pytest.fixture
async def agents_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)
    monkeypatch.setattr(agents, "async_session_factory", factory)
    agents.invalidate_cache()
    yield factory
    agents.invalidate_cache()
    await engine.dispose()


async def _add(factory, *rows) -> None:
    async with factory() as session:
        session.add_all(rows)
        await session.commit()


def _profile(name: str, enabled: bool = True, builtin: bool = False) -> db.AgentProfile:
    return db.AgentProfile(user_id=1, name=name, display_name=name.title(), role="r", goal="g",
                           enabled=enabled, is_default=builtin, tool_permissions="{}")


class TestResolveDefaultAgent:
    async def test_unset_means_the_plain_assistant(self, agents_db):
        await _add(agents_db, _profile("architect"))
        assert await agents.resolve_default_agent_name() == "default"

    async def test_configured_agent_is_used(self, agents_db):
        await _add(agents_db, _profile("architect"), db.AppSetting(key="default_agent", value="architect"))
        assert await agents.resolve_default_agent_name() == "architect"

    @pytest.mark.parametrize("value", ["missing", "__default__", "disabled_one", ""])
    async def test_unusable_choices_fall_back(self, agents_db, value):
        await _add(agents_db, _profile("disabled_one", enabled=False), _profile("__default__"),
                   db.AppSetting(key="default_agent", value=value))
        assert await agents.resolve_default_agent_name() == "default"


class TestRemovedBuiltins:
    def test_parse(self):
        assert db.parse_removed_builtin_agents('["coder", "writer"]') == {"coder", "writer"}
        assert db.parse_removed_builtin_agents(None) == set()
        assert db.parse_removed_builtin_agents("not json") == set()
        assert db.parse_removed_builtin_agents('{"a": 1}') == set()

    async def test_seeding_skips_removed_builtins(self, agents_db):
        removed = ["researcher", "analyst"]
        await _add(agents_db, db.AppSetting(key=db.REMOVED_BUILTIN_AGENTS_SETTING, value=json.dumps(removed)))
        await db._ensure_default_agents()
        async with agents_db() as session:
            names = set((await session.execute(select(db.AgentProfile.name))).scalars())
        builtin = {item["name"] for item in db.DEFAULT_AGENTS}
        assert names == builtin - set(removed)


class TestDeleteEndpoint:
    async def _delete(self, factory, name: str):
        from crabagent.serve.api.agent import delete_agent_profile

        async with factory() as session:
            return await delete_agent_profile(name, user=None, db=session)

    async def test_deleting_a_builtin_is_remembered_and_clears_the_default(self, agents_db):
        await _add(agents_db, _profile("coder", builtin=True), _profile("architect"),
                   db.AppSetting(key="default_agent", value="coder"))
        assert (await self._delete(agents_db, "coder"))["status"] == "deleted"
        async with agents_db() as session:
            settings = {row.key: row.value for row in (await session.execute(select(db.AppSetting))).scalars()}
            names = set((await session.execute(select(db.AgentProfile.name))).scalars())
        assert names == {"architect"}
        assert json.loads(settings[db.REMOVED_BUILTIN_AGENTS_SETTING]) == ["coder"]
        assert settings["default_agent"] == ""

        # and it stays gone after the startup seeding
        await db._ensure_default_agents()
        async with agents_db() as session:
            assert not (await session.execute(select(db.AgentProfile).where(db.AgentProfile.name == "coder"))).first()

    async def test_internal_profile_cannot_be_deleted(self, agents_db):
        from fastapi import HTTPException

        await _add(agents_db, _profile("__default__", builtin=True))
        with pytest.raises(HTTPException) as err:
            await self._delete(agents_db, "__default__")
        assert err.value.status_code == 403

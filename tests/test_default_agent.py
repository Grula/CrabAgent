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

    async def test_seeded_team_gets_its_own_skills(self, agents_db):
        await db._ensure_default_agents()
        async with agents_db() as session:
            rows = {r.name: r for r in (await session.execute(select(db.AgentProfile))).scalars()}
        assert set(rows) == {"architect", "implementer", "tester", "the_reviewer", "researcher"}
        assert "plan" in json.loads(rows["architect"].skills)
        assert "docx" not in json.loads(rows["architect"].skills)
        assert json.loads(rows["the_reviewer"].skills) == ["strict-review", "code-review", "security-review", "git-workflow"]

    async def test_seeding_never_changes_an_existing_agents_skills(self, agents_db):
        custom = _profile("architect", builtin=True)
        custom.skills = None
        await _add(agents_db, custom)
        await db._ensure_default_agents()
        async with agents_db() as session:
            row = (await session.execute(select(db.AgentProfile).where(db.AgentProfile.name == "architect"))).scalar_one()
        assert row.skills is None

    def test_every_default_skill_is_bundled(self):
        from pathlib import Path

        from crabagent.core.agent.skill.loader import discover_skills

        bundled = discover_skills([Path(db.__file__).resolve().parents[1] / "skills"])
        for agent in db.DEFAULT_AGENTS:
            missing = set(agent.get("skills", [])) - set(bundled)
            assert not missing, f"{agent['name']} lists skills that are not bundled: {missing}"


class TestDeleteEndpoint:
    async def _delete(self, factory, name: str):
        from crabagent.serve.api.agent import delete_agent_profile

        async with factory() as session:
            return await delete_agent_profile(name, user=None, db=session)

    async def test_deleting_a_builtin_is_remembered_and_clears_the_default(self, agents_db):
        await _add(agents_db, _profile("implementer", builtin=True), _profile("architect"),
                   db.AppSetting(key="default_agent", value="implementer"))
        assert (await self._delete(agents_db, "implementer"))["status"] == "deleted"
        async with agents_db() as session:
            settings = {row.key: row.value for row in (await session.execute(select(db.AppSetting))).scalars()}
            names = set((await session.execute(select(db.AgentProfile.name))).scalars())
        assert names == {"architect"}
        assert json.loads(settings[db.REMOVED_BUILTIN_AGENTS_SETTING]) == ["implementer"]
        assert settings["default_agent"] == ""

        # and it stays gone after the startup seeding
        await db._ensure_default_agents()
        async with agents_db() as session:
            assert not (await session.execute(select(db.AgentProfile).where(db.AgentProfile.name == "implementer"))).first()

    async def test_internal_profile_cannot_be_deleted(self, agents_db):
        from fastapi import HTTPException

        await _add(agents_db, _profile("__default__", builtin=True))
        with pytest.raises(HTTPException) as err:
            await self._delete(agents_db, "__default__")
        assert err.value.status_code == 403


class TestDefaultTeamCapabilities:
    """The shipped team gets the tools its jobs need, not email or the calendar."""

    def _caps(self, name: str) -> list[str]:
        agent = next(a for a in db.DEFAULT_AGENTS if a["name"] == name)
        return agents.agent_capabilities(agent["tool_permissions"])

    def test_no_default_agent_handles_email_calendar_or_schedules(self):
        for agent in db.DEFAULT_AGENTS:
            caps = self._caps(agent["name"])
            assert not {"email", "calendar", "scheduled tasks"} & set(caps), (agent["name"], caps)

    def test_only_implementer_and_tester_use_the_shared_browser(self):
        users = {a["name"] for a in db.DEFAULT_AGENTS if "shared browser" in self._caps(a["name"])}
        assert users == {"implementer", "tester"}

    def test_each_agent_keeps_what_its_job_needs(self):
        assert {"edit files", "run commands"} <= set(self._caps("implementer"))
        assert {"edit files", "run commands"} <= set(self._caps("tester"))
        assert {"run commands", "git diff"} <= set(self._caps("the_reviewer"))
        assert "edit files" not in self._caps("the_reviewer")
        assert "web search" in self._caps("researcher")
        assert not {"edit files", "run commands", "web search"} & set(self._caps("architect"))

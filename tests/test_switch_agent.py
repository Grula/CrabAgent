"""switch_agent hands a chat to another agent, and the General front-desk agent uses it."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import crabagent.core  # noqa: F401  (registers the built-in tools)
import crabagent.core.agent.agents as agents
import crabagent.core.agent.tools.agent as agent_tools
import crabagent.core.database as db
from crabagent.core.agent.agent_switch import filter_tool_registry
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.registry import registry
from crabagent.core.event import EventType

GENERAL = next(a for a in db.DEFAULT_AGENTS if a["name"] == "general")


@pytest.fixture
async def chat_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)
    monkeypatch.setattr(agents, "async_session_factory", factory)
    monkeypatch.setattr(agent_tools, "async_session_factory", factory)
    agents.invalidate_cache()
    async with factory() as session:
        session.add_all([
            db.Conversation(session_id="s1", user_id=1, agent="general"),
            _profile("general"),
            _profile("architect"),
            _profile("retired", enabled=False),
            _profile("__default__"),
        ])
        await session.commit()
    yield factory
    agents.invalidate_cache()
    await engine.dispose()


def _profile(name: str, enabled: bool = True) -> db.AgentProfile:
    return db.AgentProfile(user_id=1, name=name, display_name=name.title(), role="r", goal="g",
                           enabled=enabled, tool_permissions="{}")


def _context(session_id: str | None = "s1", agent: str = "general") -> tuple[AgentContext, list]:
    ctx = AgentContext(workspace=Path("."), current_agent=agent)
    if session_id:
        ctx.metadata["session_id"] = session_id
    events: list = []

    async def listen(event):
        events.append(event)

    ctx.event_bus.subscribe(listen)
    return ctx, events


async def _chat_agent(factory) -> str:
    async with factory() as session:
        return await session.scalar(select(db.Conversation.agent).where(db.Conversation.session_id == "s1"))


class TestSwitchAgent:
    async def test_hands_the_chat_over_and_tells_the_ui(self, chat_db):
        ctx, events = _context()
        result = await agent_tools.switch_agent("architect", "Add a --models flag to bench.py", context=ctx)

        assert await _chat_agent(chat_db) == "architect"
        switched = [e for e in events if e.type == EventType.AGENT_SWITCHED]
        assert len(switched) == 1
        assert switched[0].data["agent"] == "architect"
        assert switched[0].data["session_id"] == "s1"
        assert switched[0].data["summary"] == "Add a --models flag to bench.py"
        assert "next message" in result and "Don't start the work yourself" in result

    @pytest.mark.parametrize("name", ["missing", "retired", "__default__", "", "general"])
    async def test_refuses_agents_it_cannot_switch_to(self, chat_db, name):
        ctx, events = _context()
        result = await agent_tools.switch_agent(name, "x", context=ctx)

        assert result.startswith("Error")
        assert await _chat_agent(chat_db) == "general"
        assert not events

    async def test_needs_a_chat_session(self, chat_db):
        ctx, _ = _context(session_id=None)
        assert (await agent_tools.switch_agent("architect", "x", context=ctx)).startswith("Error")
        assert (await agent_tools.switch_agent("architect", "x")).startswith("Error")
        assert await _chat_agent(chat_db) == "general"


def test_sub_agents_never_get_switch_agent():
    # Only the chat's main agent may hand the chat over.
    assert "switch_agent" in agents.get_delegation_tools()
    sub = filter_tool_registry(registry, allow_delegation=False)
    assert "switch_agent" not in {t.name for t in sub.list_tools()}


class TestGeneralAgent:
    def test_tools_are_read_look_up_and_switch(self):
        tools = {t.name for t in filter_tool_registry(
            registry, tool_permissions=GENERAL["tool_permissions"], allow_delegation=True
        ).list_tools()}
        assert {"read", "grep", "glob", "git_diff", "web_search", "web_scrape", "bash", "switch_agent"} <= tools
        assert not {"edit", "write", "delegate_task", "delegate_parallel", "run_pipeline", "handoff_to",
                    "plan_task"} & tools

    def test_commands_ask_first_and_files_never_change(self):
        perms = GENERAL["tool_permissions"]
        assert perms["bash"] == "confirm" and perms["run_tests"] == "confirm"
        caps = agents.agent_capabilities(perms)
        assert "edit files" not in caps
        assert "run commands (asks first)" in caps

    def test_code_questions_go_to_code_search_then_grep(self):
        goal = GENERAL["goal"]
        assert "code_search first for a definition" in goal
        assert "grep for exact strings" in goal
        tools = {t.name for t in filter_tool_registry(
            registry, tool_permissions=GENERAL["tool_permissions"], allow_delegation=True
        ).list_tools()}
        assert "code_search" in tools

    def test_goal_sends_dev_work_to_the_engineer_or_architect_by_switching(self):
        goal = GENERAL["goal"]
        first_line = goal.split("\n", 1)[0]
        assert "switch_agent" in first_line and "engineer" in first_line and "architect" in first_line
        assert "in their own words" in goal
        assert "handoff_to are not yours" in goal


class TestAutomaticHandover:
    async def test_records_the_switch_for_the_chat_endpoint(self, chat_db):
        ctx, _ = _context()
        await agent_tools.switch_agent("architect", "Add a --models flag", context=ctx)
        assert ctx.metadata["_switched_to"] == {
            "agent": "architect", "display_name": "Architect", "summary": "Add a --models flag", "from": "general",
        }

    async def test_web_chat_says_the_new_agent_starts_now(self, chat_db):
        ctx, _ = _context()
        ctx.metadata["_auto_handover"] = True
        result = await agent_tools.switch_agent("architect", "x", context=ctx)
        assert "starts on your summary as soon as you stop" in result
        assert "next message" not in result

    async def test_a_refused_switch_records_nothing(self, chat_db):
        ctx, _ = _context()
        await agent_tools.switch_agent("missing", "x", context=ctx)
        assert "_switched_to" not in ctx.metadata


def test_handover_request_carries_the_summary_and_the_settings():
    from crabagent.serve.api.prompt import HANDOVER_PREFIX, PromptRequest, _handover_request

    req = PromptRequest(message="fix it", model="m", provider="p", agent="general", reasoning_effort="high",
                        workspace_type="code", work_mode=True)
    follow = _handover_request(req, {"agent": "engineer", "summary": "Fix the 200k bug", "from": "general"})
    assert follow.agent == "engineer"
    assert follow.message.startswith(HANDOVER_PREFIX + "general handed this request over to you")
    assert follow.message.endswith("Fix the 200k bug")
    assert (follow.model, follow.provider, follow.reasoning_effort, follow.work_mode) == ("m", "p", "high", True)


def test_handover_chains_stop_after_the_limit():
    from crabagent.serve.api import prompt as prompt_api

    prompt_api._handover_chain.clear()
    # A user message always starts a new chain.
    assert prompt_api._next_handover_depth("s", "please fix") == 1
    prompt_api._handover_chain["s"] = 1
    assert prompt_api._next_handover_depth("s", prompt_api.HANDOVER_PREFIX + "x") == 2
    prompt_api._handover_chain["s"] = prompt_api.MAX_CHAINED_HANDOVERS
    assert prompt_api._next_handover_depth("s", prompt_api.HANDOVER_PREFIX + "x") is None  # agents ping-ponging
    assert prompt_api._next_handover_depth("s", "user again") == 1
    prompt_api._handover_chain.clear()

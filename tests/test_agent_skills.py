"""Each agent is offered only its own skills (agent_profiles.skills)."""

from __future__ import annotations

from pathlib import Path

import pytest

from crabagent.core.agent import agents as agents_module
from crabagent.core.agent.agents import parse_agent_skills
from crabagent.core.agent.skill.loader import SkillInfo, register_skill_tool, scope_skill_tool
from crabagent.core.agent.tools.registry import ToolRegistry


def _skills(*names: str) -> dict[str, SkillInfo]:
    return {
        n: SkillInfo(name=n, description=f"{n} playbook", content=f"# {n} body", location=Path(f"/skills/{n}"))
        for n in names
    }


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    register_skill_tool(registry, _skills(*names))
    return registry


def _offered(registry: ToolRegistry) -> list[str]:
    tool = registry.get("skill")
    if tool is None:
        return []
    listing = tool.description.split("Available skills:\n", 1)[1]
    return [line.split("**")[1] for line in listing.splitlines()]


class TestScopeSkillTool:
    def test_no_list_keeps_every_skill(self):
        registry = _registry("plan", "docx", "verify")
        scope_skill_tool(registry, None)
        assert _offered(registry) == ["docx", "plan", "verify"]

    def test_list_narrows_what_is_offered_and_loadable(self):
        registry = _registry("plan", "docx", "verify")
        scope_skill_tool(registry, ["plan", "verify"])

        assert _offered(registry) == ["plan", "verify"]
        load = registry.get("skill").handler
        assert "# plan body" in load("plan")
        assert load("docx").startswith("Error: skill 'docx' not found. Available skills: plan, verify")

    def test_empty_list_or_unknown_names_remove_the_tool(self):
        for allowed in ([], ["not-installed"]):
            registry = _registry("plan", "docx")
            scope_skill_tool(registry, allowed)
            assert registry.get("skill") is None

    def test_a_narrowed_tool_can_be_scoped_again_from_the_full_pool(self):
        # The chat agent narrows its copy; a sub-agent built from that tool must
        # still be able to get skills the chat agent was not offered.
        chat = _registry("plan", "docx", "verify")
        scope_skill_tool(chat, ["plan"])
        sub = chat.clone()

        scope_skill_tool(sub, ["verify"])

        assert _offered(chat) == ["plan"]
        assert _offered(sub) == ["verify"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("not json", None), ('{"a": 1}', None), ("[]", []), ('["plan"]', ["plan"])],
)
def test_parse_agent_skills(raw, expected):
    assert parse_agent_skills(raw) == expected


async def test_sub_agent_gets_only_its_own_skills(monkeypatch: pytest.MonkeyPatch):
    from crabagent.core.agent.context import AgentContext

    agent_def = {
        "name": "the_reviewer", "display_name": "Reviewer", "role": "r", "goal": "g", "backstory": "b",
        "tool_permissions": {}, "skills": ["strict-review", "code-review"],
    }

    async def returns(value):
        return value

    monkeypatch.setattr(agents_module, "get_agent", lambda *a, **k: returns(agent_def))
    monkeypatch.setattr(agents_module, "_load_shared_context", lambda *a, **k: returns(""))
    monkeypatch.setattr(agents_module, "load_sub_agent_model_map", lambda *a, **k: returns({}))
    seen: list[list[str]] = []

    async def run_agent(sub_context, task, *args, **kwargs):
        seen.append(_offered(sub_context.tool_registry))
        sub_context.messages.append({"role": "assistant", "content": "done"})
        return sub_context.messages

    monkeypatch.setattr(agents_module, "run_agent", run_agent, raising=False)
    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", run_agent)
    agents_module._running_sub_agents.clear()
    parent = AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0})
    parent.tool_registry = _registry("plan", "docx", "strict-review", "code-review")

    await agents_module.spawn_sub_agent(parent, agent_name="the_reviewer", task="x", agent_def=agent_def)

    assert seen == [["code-review", "strict-review"]]
    assert _offered(parent.tool_registry) == ["code-review", "docx", "plan", "strict-review"]


async def test_sub_agent_inherits_unsaved_editor_warning_and_its_compaction_cap(monkeypatch: pytest.MonkeyPatch):
    from crabagent.core.agent.context import AgentContext

    agent_def = {
        "name": "coder", "display_name": "Coder", "role": "r", "goal": "g", "backstory": "",
        "tool_permissions": {}, "skills": [], "compaction_max_tokens": 120_000,
    }

    async def returns(value):
        return value

    monkeypatch.setattr(agents_module, "_load_shared_context", lambda *a, **k: returns(""))
    monkeypatch.setattr(agents_module, "load_sub_agent_model_map", lambda *a, **k: returns({}))
    seen: list = []

    async def run_agent(sub_context, task, *args, **kwargs):
        seen.append((sub_context.system_prompt, dict(sub_context.metadata)))
        sub_context.messages.append({"role": "assistant", "content": "done"})
        return sub_context.messages

    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", run_agent)
    agents_module._running_sub_agents.clear()
    parent = AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0, "unsaved_editor_file": "src/app.py"})
    parent.tool_registry = _registry()

    await agents_module.spawn_sub_agent(parent, agent_name="coder", task="x", agent_def=agent_def)

    prompt, metadata = seen[0]
    assert "unsaved edits in `src/app.py`" in prompt
    assert metadata["unsaved_editor_file"] == "src/app.py"  # passed on to its own sub-agents
    assert metadata["compaction_max_tokens"] == 120_000

    seen.clear()
    clean = AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0})
    clean.tool_registry = _registry()
    await agents_module.spawn_sub_agent(clean, agent_name="coder", task="x", agent_def={**agent_def})
    assert "Unsaved editor changes" not in seen[0][0]


# --- API --------------------------------------------------------------------------


@pytest.fixture
async def agents_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from crabagent.core import database as db

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agents.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def test_api_sets_narrows_and_resets_skills(agents_db):
    from crabagent.serve.api import agent as api

    async with agents_db() as session:
        created = await api.create_agent_profile(
            api.CreateAgentRequest(name="helper", display_name="Helper", role="r", goal="g", skills=["verify", "plan", "plan"]),
            user=type("U", (), {"id": 1})(), db=session,
        )
    assert created.skills == ["plan", "verify"]

    async with agents_db() as session:
        kept = await api.update_agent_profile("helper", api.UpdateAgentRequest(goal="g2"), user=None, db=session)
    assert kept.skills == ["plan", "verify"]  # leaving the field out keeps the list

    async with agents_db() as session:
        reset = await api.update_agent_profile("helper", api.UpdateAgentRequest(skills=None), user=None, db=session)
    assert reset.skills is None  # explicit null: every skill again

    async with agents_db() as session:
        none = await api.update_agent_profile("helper", api.UpdateAgentRequest(skills=[]), user=None, db=session)
    assert none.skills == []


async def test_api_sets_and_clears_the_compaction_cap(agents_db):
    from fastapi import HTTPException

    from crabagent.serve.api import agent as api

    async with agents_db() as session:
        created = await api.create_agent_profile(
            api.CreateAgentRequest(name="lead", display_name="Lead", role="r", goal="g", compaction_max_tokens=200_000),
            user=type("U", (), {"id": 1})(), db=session,
        )
    assert created.compaction_max_tokens == 200_000

    async with agents_db() as session:
        kept = await api.update_agent_profile("lead", api.UpdateAgentRequest(goal="g2"), user=None, db=session)
    assert kept.compaction_max_tokens == 200_000  # left out: unchanged

    async with agents_db() as session:
        with pytest.raises(HTTPException) as too_small:
            await api.update_agent_profile(
                "lead", api.UpdateAgentRequest(compaction_max_tokens=500), user=None, db=session
            )
    assert too_small.value.status_code == 400

    async with agents_db() as session:
        cleared = await api.update_agent_profile(
            "lead", api.UpdateAgentRequest(compaction_max_tokens=None), user=None, db=session
        )
    assert cleared.compaction_max_tokens is None  # explicit null: the global setting again


async def test_profile_cap_reaches_the_chat_context(monkeypatch: pytest.MonkeyPatch):
    from crabagent.core.agent.context import AgentContext
    from crabagent.serve.api import prompt as prompt_api

    async def fake_get_agent(name):
        return {"name": name, "tool_permissions": {}, "skills": None, "compaction_max_tokens": 150_000}

    monkeypatch.setattr(agents_module, "get_agent", fake_get_agent)
    context = AgentContext(workspace=Path("."))
    await prompt_api.apply_agent_profile(context, "architect")
    assert context.metadata["compaction_max_tokens"] == 150_000


# --- Chat mode ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "profile", "offered", "sub_pool"),
    [
        ("architect", {"name": "architect", "tool_permissions": {"bash": "deny"}, "skills": ["plan"]},
         ["plan"], ["docx", "plan", "verify"]),
        ("architect", {"name": "architect", "tool_permissions": {}, "skills": None},
         ["docx", "plan", "verify"], ["docx", "plan", "verify"]),
        ("default", {"name": "__default__", "tool_permissions": {}, "skills": ["docx"]},
         ["docx"], ["docx", "plan", "verify"]),
    ],
)
async def test_chat_agent_sees_only_its_skills(monkeypatch: pytest.MonkeyPatch, agent, profile, offered, sub_pool):
    from crabagent.core.agent.context import AgentContext
    from crabagent.serve.api import prompt as prompt_api

    async def get_agent(name):
        return profile

    monkeypatch.setattr(agents_module, "get_agent", get_agent)
    context = AgentContext(workspace=Path("/tmp"))
    context.tool_registry = _registry("plan", "docx", "verify")
    context.tool_registry._tools["bash"] = context.tool_registry.get("skill")  # any second tool

    await prompt_api.apply_agent_profile(context, agent)

    assert _offered(context.tool_registry) == offered
    # sub-agents are cut from the unnarrowed pool
    assert _offered(context.base_tool_registry or context.tool_registry) == sub_pool
    if profile["tool_permissions"].get("bash") == "deny":
        assert context.tool_registry.get("bash") is None


# --- Project skills come from the chat's own project ----------------------------------


def test_project_skills_follow_the_chats_workspace(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from crabagent.core.config import settings

    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # no global skills in the way

    for project in ("alpha", "beta"):
        skill = tmp_path / project / ".crabagent" / "skills" / "run-app"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: run-app\ndescription: run {project}\n---\nbody")
    from crabagent.core.agent.skill.loader import discover_skills

    alpha = discover_skills(settings.skill_discovery_dirs(tmp_path / "alpha"))
    beta = discover_skills(settings.skill_discovery_dirs(tmp_path / "beta"))
    empty = discover_skills(settings.skill_discovery_dirs(tmp_path))

    assert alpha["run-app"].description == "run alpha"
    assert beta["run-app"].description == "run beta"
    # a project without its own run guide gets the general one CrabAgent ships
    assert empty["run-app"].location.parent.name == "skills"
    assert tmp_path not in empty["run-app"].location.parents


# --- request_help: a delegated agent can ask one teammate ----------------------------


async def test_delegated_agent_can_ask_a_teammate_once(monkeypatch: pytest.MonkeyPatch):
    from crabagent.core.agent.context import AgentContext
    from crabagent.core.agent.tools import agent as agent_tools

    team = {
        "coder": {"name": "coder", "display_name": "Coder", "role": "Software Engineer", "goal": "g",
                  "backstory": "", "allow_delegation": True,
                  "tool_permissions": {"web_search": "deny", "web_scrape": "deny"}},
        "researcher": {"name": "researcher", "display_name": "Researcher", "role": "Web Researcher", "goal": "g",
                       "backstory": "", "tool_permissions": {}, "allow_delegation": True},
    }

    async def get_agent(name):
        return team.get(name)

    async def registry():
        return list(team.values())

    async def returns(value):
        return value

    monkeypatch.setattr(agents_module, "get_agent", get_agent)
    monkeypatch.setattr(agents_module, "load_agent_registry", registry)
    monkeypatch.setattr(agents_module, "_load_shared_context", lambda *a, **k: returns(""))
    monkeypatch.setattr(agents_module, "load_sub_agent_model_map", lambda *a, **k: returns({}))
    seen: dict[str, object] = {}

    async def run_agent(sub_context, task, *args, **kwargs):
        name = sub_context.current_agent
        seen[name] = sub_context
        if name == "coder":
            answer = await agent_tools.request_help("researcher", "std::span::subspan bounds?", context=sub_context)
            seen["answer"] = answer
            # the helper cannot ask anyone in turn
            seen["helper_asks"] = await agent_tools.request_help("coder", "x", context=seen["researcher"])
        sub_context.messages.append({"role": "assistant", "content": f"{name} done"})
        return sub_context.messages

    monkeypatch.setattr(agents_module, "run_agent", run_agent, raising=False)
    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", run_agent)
    agents_module._running_sub_agents.clear()
    parent = AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0})
    parent.tool_registry = ToolRegistry()
    parent.tool_registry._tools["request_help"] = agent_tools.registry.get("request_help")

    await agents_module.spawn_sub_agent(parent, agent_name="coder", task="fix it", agent_def=team["coder"])

    coder = seen["coder"]
    assert "request_help" in coder.tool_registry._tools
    assert "Teammates who can do what you can't: `researcher` (web search)." in coder.system_prompt
    assert "researcher done" in seen["answer"]
    assert "request_help" not in seen["researcher"].tool_registry._tools
    assert seen["helper_asks"].startswith("Error: cannot request help")


# --- Deny means deny, memory tools included -------------------------------------------


def _memory_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("memory_save", "memory_recall", "memory_forget", "read"):
        registry._tools[name] = type("T", (), {"name": name, "requires_permission": False})()
    return registry


def test_chat_agent_does_not_get_a_denied_memory_tool():
    from crabagent.core.agent.agent_switch import filter_tool_registry

    narrowed = filter_tool_registry(_memory_registry(), tool_permissions={"memory_forget": "deny"}, allow_delegation=True)

    assert "memory_forget" not in narrowed._tools
    assert {"memory_save", "memory_recall"} <= set(narrowed._tools)


def test_memory_tools_still_survive_a_tool_whitelist():
    from crabagent.core.agent.agent_switch import filter_tool_registry

    narrowed = filter_tool_registry(_memory_registry(), agent_tools=["read"], allow_delegation=True)

    assert {"read", "memory_save", "memory_forget"} <= set(narrowed._tools)


async def test_sub_agent_does_not_get_a_denied_memory_tool(monkeypatch: pytest.MonkeyPatch):
    from crabagent.core.agent.context import AgentContext

    agent_def = {"name": "coder", "display_name": "Coder", "role": "r", "goal": "g", "backstory": "",
                 "tool_permissions": {"memory_forget": "deny"}, "allow_delegation": False}

    async def returns(value):
        return value

    monkeypatch.setattr(agents_module, "get_agent", lambda *a, **k: returns(agent_def))
    monkeypatch.setattr(agents_module, "_load_shared_context", lambda *a, **k: returns(""))
    monkeypatch.setattr(agents_module, "load_sub_agent_model_map", lambda *a, **k: returns({}))
    seen = {}

    async def run_agent(sub_context, task, *args, **kwargs):
        seen["tools"] = set(sub_context.tool_registry._tools)
        sub_context.messages.append({"role": "assistant", "content": "done"})
        return sub_context.messages

    monkeypatch.setattr(agents_module, "run_agent", run_agent, raising=False)
    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", run_agent)
    agents_module._running_sub_agents.clear()
    parent = AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0})
    parent.tool_registry = _memory_registry()

    await agents_module.spawn_sub_agent(parent, agent_name="coder", task="x", agent_def=agent_def)

    assert "memory_forget" not in seen["tools"]
    assert {"memory_save", "memory_recall", "read"} <= seen["tools"]


def test_each_missing_capability_goes_to_one_specialist():
    from crabagent.core.agent.agents import helpers_for

    no_web = {"web_search": "deny", "web_scrape": "deny", "browser": "deny", "create_tool": "deny"}
    coder = {"name": "coder", "tool_permissions": no_web}
    team = [
        coder,
        {"name": "architect", "tool_permissions": {**no_web, "bash": "deny", "edit": "deny", "write": "deny"}},
        {"name": "optimizer", "tool_permissions": {"create_tool": "deny"}},  # can search, but does everything
        {"name": "ml_researcher", "tool_permissions": {"web_search": "confirm", "web_scrape": "confirm",
                                                       "bash": "deny", "edit": "deny", "write": "deny",
                                                       "create_tool": "deny"}},  # asks first: not preferred
        {"name": "researcher", "tool_permissions": {"bash": "deny", "edit": "deny", "write": "deny",
                                                    "create_tool": "deny", "email_check": "deny",
                                                    "email_send": "deny"}},
        {"name": "toolsmith", "tool_permissions": {}},
    ]

    helpers = helpers_for(coder, team)

    assert helpers == [
        {"name": "researcher", "adds": ["web search", "browser"]},
        {"name": "toolsmith", "adds": ["building tools"]},
    ]


def test_an_agent_that_lacks_nothing_gets_no_helper_line():
    from crabagent.core.agent.agents import helpers_for

    assert helpers_for({"name": "a", "tool_permissions": {}}, [{"name": "b", "tool_permissions": {"bash": "deny"}}]) == []


def test_the_natural_owner_wins_a_tie():
    from crabagent.core.agent.agents import helpers_for

    coder = {"name": "coder", "tool_permissions": {"web_search": "deny", "web_scrape": "deny"}}
    same_size = {"bash": "deny", "edit": "deny", "write": "deny"}
    team = [{"name": "optimizer", "tool_permissions": same_size}, {"name": "researcher", "tool_permissions": same_size}]

    assert helpers_for(coder, team) == [{"name": "researcher", "adds": ["web search"]}]


def test_a_read_only_agent_is_not_offered_someone_to_edit_files():
    from crabagent.core.agent.agents import helpers_for

    reviewer = {"name": "the_reviewer", "tool_permissions": {"edit": "deny", "write": "deny"}}
    coder = {"name": "coder", "tool_permissions": {}}

    assert all("edit files" not in h["adds"] for h in helpers_for(reviewer, [coder]))


def test_nobody_is_offered_as_a_git_diff_helper():
    from crabagent.core.agent.agents import helpers_for

    researcher = {"name": "ml_researcher", "tool_permissions": {"git_diff": "deny", "bash": "deny"}}
    architect = {"name": "architect", "tool_permissions": {"bash": "deny", "edit": "deny", "write": "deny"}}

    assert helpers_for(researcher, [architect]) == []


# --- Shared notes injected into delegated agents are capped ----------------------------


async def test_shared_notes_keep_the_newest_within_a_budget(monkeypatch: pytest.MonkeyPatch):
    notes = [{"key": f"note-{i}", "value": "x" * 1500, "author": "coder"} for i in range(20)]

    async def get_all(session_id):
        return notes

    monkeypatch.setattr("crabagent.core.database.shared_memory_get_all", get_all)
    block = await agents_module._load_shared_context("s")

    assert "### note-19" in block and "### note-15" in block  # newest kept
    assert "### note-0 " not in block and "### note-0\n" not in block
    assert "older note(s) not shown" in block and "`note-0`" in block
    assert len(block) < agents_module._SHARED_CONTEXT_BUDGET + 2000


async def test_a_single_huge_note_is_cut_with_a_pointer(monkeypatch: pytest.MonkeyPatch):
    async def get_all(session_id):
        return [{"key": "dump", "value": "y" * 20000, "author": ""}]

    monkeypatch.setattr("crabagent.core.database.shared_memory_get_all", get_all)
    block = await agents_module._load_shared_context("s")

    assert "shared_get('dump')" in block
    assert len(block) < agents_module._SHARED_CONTEXT_BUDGET + 500


async def test_small_sessions_are_unchanged(monkeypatch: pytest.MonkeyPatch):
    async def get_all(session_id):
        return [{"key": "scope", "value": "Only src/api.", "author": "architect"}]

    monkeypatch.setattr("crabagent.core.database.shared_memory_get_all", get_all)
    block = await agents_module._load_shared_context("s")

    assert "### scope (by architect)\nOnly src/api." in block
    assert "not shown" not in block

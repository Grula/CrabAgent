"""Contract tests for the prompt fragments owned by ``core/agent/agents.py``.

These pin two behaviours of the agent prompt text:

1. Sub-agent prompts carry a single shared *execution contract* (budget, cwd,
   tool-call markup, REMAINING list, independent work) and no longer carry the
   old "Be thorough" wording. The chat agent-switch message does not carry it.
2. The team prompt states the pipeline granularity rule.
"""
from __future__ import annotations

import pytest

from crabagent.core.agent import agents as agents_module

# Phrases that must appear in the shared execution contract.
EXECUTION_CONTRACT_PHRASES: tuple[str, ...] = (
    "limited tool-iteration budget",
    "Confirm your working directory before editing",
    "never print tool-call markup as text",
    "REMAINING list",
    "work independently",
)

# Wording that must be gone from the prompt fragments.
REMOVED_PHRASE = "Be thorough"

PIPELINE_GRANULARITY_TEXT = (
    "One pipeline step = one file or one concern. "
    "If a step has more than ~5 deliverables, split it into separate steps."
)

# Prefix of the agent-switch footer, which ends the switch message.
SWITCH_TOOL_LIST_PREFIX = "Your available tools are the ones in this session's tool list"

AGENT_DEF = {
    "name": "coder",
    "display_name": "Code Expert",
    "role": "Write code",
    "goal": "Ship clean code",
    "backstory": "Senior engineer",
    "icon": "💻",
}


def _contract() -> str:
    """Return the module-level execution contract, failing if it is missing."""
    value = getattr(agents_module, "_AGENT_EXECUTION_CONTRACT", None)
    assert isinstance(value, str) and value.strip(), (
        "agents.py must define a non-empty module constant _AGENT_EXECUTION_CONTRACT"
    )
    return value


def _granularity() -> str:
    """Return the module-level pipeline granularity rule, failing if missing."""
    value = getattr(agents_module, "_PIPELINE_GRANULARITY", None)
    assert isinstance(value, str) and value.strip(), (
        "agents.py must define a non-empty module constant _PIPELINE_GRANULARITY"
    )
    return value


# ── _AGENT_EXECUTION_CONTRACT ─────────────────────────────────────────


class TestExecutionContractConstant:
    @pytest.mark.parametrize("phrase", EXECUTION_CONTRACT_PHRASES)
    def test_contains_required_phrase(self, phrase: str):
        assert phrase in _contract()

    def test_does_not_contain_be_thorough(self):
        assert REMOVED_PHRASE not in _contract()


# ── _build_system_prompt ──────────────────────────────────────────────


class TestSystemPromptContract:
    def test_includes_execution_contract(self):
        prompt = agents_module._build_system_prompt(AGENT_DEF)
        assert _contract() in prompt

    def test_excludes_be_thorough(self):
        prompt = agents_module._build_system_prompt(AGENT_DEF)
        assert REMOVED_PHRASE not in prompt


# ── build_agent_switch_msg ────────────────────────────────────────────


class TestAgentSwitchMsgContract:
    # The switch message is for the main chat agent, which talks to the user;
    # the sub-agent contract ("don't stop to ask questions", REMAINING report)
    # contradicts the main system prompt there.
    def test_excludes_execution_contract(self):
        content = agents_module.build_agent_switch_msg(AGENT_DEF, extra_lines=None)["content"]
        assert _contract() not in content
        assert "work independently" not in content

    def test_ends_with_tool_list_note(self):
        content = agents_module.build_agent_switch_msg(AGENT_DEF, extra_lines=None)["content"]
        assert SWITCH_TOOL_LIST_PREFIX in content, (
            "agent-switch footer wording changed; update this test deliberately"
        )
        assert content.rstrip().splitlines()[-1].startswith(SWITCH_TOOL_LIST_PREFIX)


# ── _PIPELINE_GRANULARITY ─────────────────────────────────────────────


class TestPipelineGranularityConstant:
    def test_exact_sentence(self):
        assert _granularity() == PIPELINE_GRANULARITY_TEXT


# ── build_team_prompt ─────────────────────────────────────────────────


class TestTeamPromptPipelineGranularity:
    @pytest.mark.asyncio
    async def test_team_prompt_includes_granularity_rule(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_load_agent_registry():
            return [
                {
                    "name": "coder",
                    "display_name": "Code Expert",
                    "role": "Write code",
                    "goal": "Ship clean code",
                    "backstory": "",
                    "tool_permissions": {},
                }
            ]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_load_agent_registry)

        prompt = await agents_module.build_team_prompt()

        assert prompt, "team prompt should not be empty when agents are registered"
        assert PIPELINE_GRANULARITY_TEXT in prompt


# ── sub-agent environment line and delegation scope ───────────────────


class TestSubAgentEnvironment:
    def test_prompt_states_date_and_working_directory_when_workspace_is_known(self, tmp_path):
        prompt = agents_module._build_system_prompt(AGENT_DEF, workspace=tmp_path)

        assert f"Working directory: {tmp_path}" in prompt
        assert "Today is " in prompt
        # The environment line comes before the working rules.
        assert prompt.index("Working directory:") < prompt.index(_contract())

    def test_prompt_omits_environment_line_without_workspace(self):
        prompt = agents_module._build_system_prompt(AGENT_DEF)

        assert "Working directory:" not in prompt


class TestDelegationScope:
    def test_pipeline_and_planning_tools_are_delegation_tools(self):
        # Sub-agents must not be able to spawn further agents through a pipeline.
        tools = agents_module.get_delegation_tools()

        assert {"run_pipeline", "plan_task", "delegate_task", "delegate_parallel"} <= tools


# ── team prompt: real agents only, compact permission lists ───────────


def _agent(name: str, **overrides) -> dict:
    base = {
        "name": name,
        "display_name": name.title(),
        "role": f"{name} role",
        "goal": f"{name} goal",
        "backstory": "",
        "tool_permissions": {},
    }
    base.update(overrides)
    return base


class TestTeamPromptAgents:
    @pytest.mark.asyncio
    async def test_internal_profiles_are_not_listed_as_agents(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_registry():
            return [_agent("__default__"), _agent("implementer")]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)

        prompt = await agents_module.build_team_prompt()

        assert "`implementer`" in prompt
        assert "__default__" not in prompt

    @pytest.mark.asyncio
    async def test_team_prompt_is_empty_when_only_internal_profiles_exist(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_registry():
            return [_agent("__default__")]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)

        assert await agents_module.build_team_prompt() == ""

    @pytest.mark.asyncio
    async def test_routing_hints_name_only_agents_that_exist(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_registry():
            return [_agent("researcher"), _agent("implementer")]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)

        prompt = await agents_module.build_team_prompt()

        assert "-> `researcher`" in prompt
        assert "`coder`" not in prompt and "`writer`" not in prompt and "`analyst`" not in prompt
        assert "`scout`" not in prompt
        assert "pick the agent whose role and goal above fit it best" in prompt

    @pytest.mark.asyncio
    async def test_code_questions_are_routed_to_the_scout(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_registry():
            return [_agent("scout"), _agent("coder")]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)

        prompt = await agents_module.build_team_prompt()

        assert "how it works in the code -> `scout`" in prompt

    @pytest.mark.asyncio
    async def test_spawning_an_internal_profile_is_refused(self):
        result = await agents_module.spawn_sub_agent(object(), "__default__", "do something")

        assert result == "Error: agent '__default__' not found"


class TestAgentCapabilities:
    def test_lists_what_the_profile_allows_in_plain_words(self):
        caps = agents_module.agent_capabilities(
            {"bash": "deny", "edit": "deny", "write": "deny", "web_search": "confirm", "web_scrape": "confirm",
             "browser": "deny", "collab_browser_open": "deny", "office_read": "deny", "office_edit": "deny",
             "office_create": "deny", "image_generate": "deny", "image_edit": "deny", "email_check": "deny",
             "email_send": "deny", "calendar_list": "deny", "calendar_add": "deny", "scheduled_task_list": "deny",
             "scheduled_task_create": "deny", "computer_act": "deny", "create_tool": "deny"}
        )
        assert caps == ["read files", "git diff", "web search (asks first)"]

    def test_one_allowed_tool_is_enough_for_a_capability(self):
        caps = agents_module.agent_capabilities({"write": "deny", "edit": "auto"})
        assert "edit files" in caps

    def test_browser_family_permission_counts(self):
        assert "browser" not in agents_module.agent_capabilities({"browser": "deny"})
        assert "browser" in agents_module.agent_capabilities({})


class TestTeamPromptCapabilities:
    @pytest.mark.asyncio
    async def test_shared_capabilities_are_listed_once(self, monkeypatch: pytest.MonkeyPatch):
        everything_but = {"browser": "deny", "collab_browser_open": "deny", "office_read": "deny",
                          "office_edit": "deny", "office_create": "deny", "image_generate": "deny",
                          "image_edit": "deny", "email_check": "deny", "email_send": "deny",
                          "calendar_list": "deny", "calendar_add": "deny", "scheduled_task_list": "deny",
                          "scheduled_task_create": "deny", "computer_act": "deny", "create_tool": "deny",
                          "git_diff": "deny"}

        async def fake_registry():
            return [
                _agent("implementer", tool_permissions={**everything_but, "web_search": "deny", "web_scrape": "deny"}),
                _agent("researcher", tool_permissions={**everything_but, "bash": "deny", "edit": "deny", "write": "deny"}),
            ]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)
        prompt = await agents_module.build_team_prompt()

        assert "Every agent can: read files." in prompt
        assert "(can: edit files, run commands)" in prompt
        assert "(can: web search)" in prompt
        assert "disabled tools" not in prompt

    @pytest.mark.asyncio
    async def test_an_agent_with_nothing_extra_says_so(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_registry():
            return [_agent("a", tool_permissions={"bash": "deny"}), _agent("b", tool_permissions={})]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)
        prompt = await agents_module.build_team_prompt()

        assert "(can: only what every agent can)" in prompt
        assert "(can: run commands)" in prompt


class TestTeamListGoalSummary:
    """The team list says what each agent is for; the agent's own rules stay with it."""

    def test_rules_after_the_first_line_stay_out(self):
        from crabagent.core.agent.agents import goal_summary

        goal = "Implement the change in your brief.\n- Stay inside the brief.\n- Run the tests."
        assert goal_summary(goal) == "Implement the change in your brief."

    def test_a_long_paragraph_is_cut_at_a_sentence(self):
        from crabagent.core.agent.agents import goal_summary

        goal = "Analyze past conversations and find failures. " + "Never retry a failed tool call. " * 20
        summary = goal_summary(goal)
        assert summary.startswith("Analyze past conversations and find failures. Never retry")
        assert summary.endswith("call.")  # ends on a whole sentence
        assert len(summary) <= 240

    def test_one_endless_sentence_is_truncated(self):
        from crabagent.core.agent.agents import goal_summary

        assert goal_summary("word " * 100).endswith("…")
        assert len(goal_summary("word " * 100)) <= 240

    async def test_team_list_shows_only_the_summary(self, monkeypatch: pytest.MonkeyPatch):
        from crabagent.core.agent import agents as agents_module

        async def registry():
            return [{"name": "implementer", "display_name": "Dev", "role": "Engineer",
                     "goal": "Implement the change.\n- Stay inside the brief.", "tool_permissions": {}}]

        monkeypatch.setattr(agents_module, "load_agent_registry", registry)
        prompt = await agents_module.build_team_prompt()
        assert "Engineer. Implement the change." in prompt
        assert "Stay inside the brief" not in prompt


class TestAgentsWithoutHands:
    """An agent that can't edit or run commands is told to hand that work off."""

    def test_hint_for_agents_without_edit_or_bash(self):
        lines = agents_module.capability_hint_lines({"read", "grep", "delegate_task"})
        assert any("can't edit files or run commands" in line for line in lines)

    @pytest.mark.parametrize("tool", ["edit", "write", "bash"])
    def test_no_hint_when_the_agent_can_act(self, tool):
        lines = agents_module.capability_hint_lines({"read", tool})
        assert not any("can't edit files" in line for line in lines)

    @pytest.mark.asyncio
    async def test_team_list_does_not_order_agents_without_tools_to_edit(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_registry():
            return [_agent("coder")]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)
        prompt = await agents_module.build_team_prompt()
        assert "a small edit -> do it yourself if you have the tools for it" in prompt
        assert "when your own tools allow it" in prompt

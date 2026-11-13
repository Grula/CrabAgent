"""Contract tests for the prompt fragments owned by ``core/agent/agents.py``.

These pin two behaviours of the agent prompt text:

1. Sub-agent prompts carry a single shared *execution contract* (budget, cwd,
   tool-call markup, REMAINING list, independent work) and no longer carry the
   old "Be thorough" wording.
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

# Prefix of the agent-switch footer; the contract has to sit *before* it.
SWITCH_TOOL_LIST_PREFIX = "Your actually available tools are the ones provided"

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
    def test_includes_execution_contract(self):
        msg = agents_module.build_agent_switch_msg(AGENT_DEF, extra_lines=None)
        assert _contract() in msg["content"]

    def test_contract_precedes_tool_list_note(self):
        content = agents_module.build_agent_switch_msg(AGENT_DEF, extra_lines=None)["content"]
        assert SWITCH_TOOL_LIST_PREFIX in content, (
            "agent-switch footer wording changed; update this test deliberately"
        )
        assert content.index(_contract()) < content.index(SWITCH_TOOL_LIST_PREFIX)


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
        assert "pick the agent whose role and goal above fit it best" in prompt

    @pytest.mark.asyncio
    async def test_spawning_an_internal_profile_is_refused(self):
        result = await agents_module.spawn_sub_agent(object(), "__default__", "do something")

        assert result == "Error: agent '__default__' not found"


class TestSummarizeDeniedTools:
    def test_three_or_more_tools_of_a_family_are_collapsed(self):
        denied = [
            "bash", "edit", "office_create", "office_edit", "office_batch_edit", "image_edit",
            "image_generate", "macos_click", "macos_type", "macos_key", "macos_drag",
            "scheduled_task_create", "scheduled_task_update", "scheduled_task_delete",
            "collab_browser_click", "update_agents_md",
        ]

        summary = agents_module.summarize_denied_tools(denied)

        assert summary == (
            "bash, edit, office_* (3), image_edit, image_generate, macos_* (4), "
            "scheduled_task_* (3), collab_browser_click, update_agents_md"
        )

    def test_unrelated_tools_that_share_a_first_word_are_not_merged(self):
        assert agents_module.summarize_denied_tools(["create_tool", "update_tool", "delete_tool"]) == (
            "create_tool, update_tool, delete_tool"
        )
        assert agents_module.summarize_denied_tools([]) == ""


class TestTeamPromptSharedDenials:
    @pytest.mark.asyncio
    async def test_tools_denied_to_every_agent_are_not_repeated_per_agent(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_registry():
            return [
                _agent("implementer", tool_permissions={"pay_now": "deny", "web_search": "deny", "bash": "auto"}),
                _agent("reviewer", tool_permissions={"pay_now": "deny", "bash": "deny"}),
            ]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)

        prompt = await agents_module.build_team_prompt()

        assert "pay_now" not in prompt
        assert "(disabled tools: web_search)" in prompt
        assert "(disabled tools: bash)" in prompt

    @pytest.mark.asyncio
    async def test_a_single_agent_keeps_its_full_denied_list(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_registry():
            return [_agent("reviewer", tool_permissions={"pay_now": "deny", "bash": "deny"})]

        monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)

        assert "(disabled tools: pay_now, bash)" in await agents_module.build_team_prompt()

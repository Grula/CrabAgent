"""An agent without delegation tools gets the team roster but not the delegation how-to."""

from pathlib import Path

import pytest

from crabagent.core.agent import agents as agents_module
from crabagent.core.agent.capability_manifest import build_runtime_system_prompt, without_delegation_sections
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.registry import ToolRegistry


def _agent(name: str) -> dict:
    return {"name": name, "display_name": name.title(), "role": "r", "goal": f"{name} goal", "tool_permissions": {}}


@pytest.fixture
async def team_prompt(monkeypatch: pytest.MonkeyPatch) -> str:
    async def fake_registry():
        return [_agent("architect"), _agent("coder")]

    monkeypatch.setattr(agents_module, "load_agent_registry", fake_registry)
    section = await agents_module.build_team_prompt()
    return "<!-- marker -->\nBase prompt.\n\n" + section + "\n## Project Rules (AGENTS.md)\nrules here\n"


def _ctx(prompt: str, *tools: str) -> AgentContext:
    registry = ToolRegistry()
    for name in tools:
        registry.register(name=name, description=name, parameters={"type": "object", "properties": {}})(
            lambda context=None: ""
        )
    return AgentContext(workspace=Path("."), system_prompt=prompt, tool_registry=registry)


async def test_a_non_delegating_agent_keeps_the_roster_only(team_prompt):
    out = without_delegation_sections(team_prompt, _ctx(team_prompt, "read", "switch_agent"))
    assert "### Available Agents" in out and "(`architect`)" in out
    assert "You can't delegate to them" in out
    for gone in ("### Delegation Tools", "### When to Delegate", "### Writing the Task", "delegate_parallel"):
        assert gone not in out
    # Everything around the team section is untouched.
    assert out.startswith("<!-- marker -->\nBase prompt.")
    assert out.endswith("## Project Rules (AGENTS.md)\nrules here\n")
    assert without_delegation_sections(out, _ctx(out)) == out  # idempotent (TUI reuses the context)


async def test_a_delegating_agent_keeps_everything(team_prompt):
    assert without_delegation_sections(team_prompt, _ctx(team_prompt, "delegate_task")) == team_prompt


async def test_the_runtime_prompt_applies_it(team_prompt, monkeypatch):
    monkeypatch.setattr("crabagent.core.agent.failure_modes.known_failure_modes", lambda: [])
    out = build_runtime_system_prompt(_ctx(team_prompt, "read"))
    assert "### Delegation Tools" not in out and "### Available Agents" in out


def test_a_prompt_without_a_team_section_is_unchanged():
    prompt = "Base prompt only.\n"
    assert without_delegation_sections(prompt, _ctx(prompt, "read")) == prompt

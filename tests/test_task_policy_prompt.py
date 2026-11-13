"""Contract tests for the shared base system prompt (core/agent/system_prompt.py).

The prompt must carry the proactive task-creation policy, render cleanly for
every interface, and be identical across the web, CLI and TUI entry points.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from crabagent.core.agent import system_prompt as sp
from crabagent.core.agent.compress import SUMMARY_PREFIX
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.loop import _build_messages


def test_system_prompt_contains_task_creation_policy():
    prompt = sp.BASE_PROMPT_TEMPLATE
    assert "task_add" in prompt
    # placeholders must survive so .format(...) works
    for placeholder in ("{date}", "{weekday}", "{workspace}", "{interface_note}", "{tool_loading_note}"):
        assert placeholder in prompt
    # proactive wording: create without being asked
    assert "do not wait to be asked" in prompt.lower()
    # scope guard: explicit no-task cases are stated
    assert "Do NOT create a task" in prompt


@pytest.mark.parametrize("interface", ["web", "terminal"])
def test_render_base_prompt_fills_every_placeholder(tmp_path, interface):
    prompt = sp.render_base_prompt(tmp_path, interface=interface, now=datetime(2026, 1, 5, tzinfo=UTC))

    assert "Today is 2026-01-05 (Monday)." in prompt
    assert f"Working directory: {tmp_path}" in prompt
    assert "{" not in prompt and "}" not in prompt
    assert sp.is_current_prompt(prompt, now=datetime(2026, 1, 5, 23, 59, tzinfo=UTC))


def test_is_current_prompt_rejects_prompts_from_older_revisions():
    assert not sp.is_current_prompt("You are CrabAgent, an AI assistant. Today is 2026-01-05 Monday.")
    assert not sp.is_current_prompt("<!-- crabagent-system-prompt r2 -->\nYou are CrabAgent")
    assert not sp.is_current_prompt("")


def test_is_current_prompt_rejects_a_prompt_built_on_an_earlier_day(tmp_path):
    monday = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    tuesday = datetime(2026, 1, 6, 9, 0, tzinfo=UTC)
    prompt = sp.render_base_prompt(tmp_path, now=monday)

    assert sp.is_current_prompt(prompt, now=monday)
    # The cached prompt still says "Today is 2026-01-05", so it must be rebuilt.
    assert not sp.is_current_prompt(prompt, now=tuesday)


def test_base_prompt_names_the_tools_it_relies_on():
    prompt = sp.BASE_PROMPT_TEMPLATE
    for tool in ("ask_question", "bash_output", "bash_stop", "molt_rollback", "todo_add", "task_update"):
        assert tool in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_assembles_sections_in_order(tmp_path, monkeypatch):
    from crabagent.core import project_memory
    from crabagent.core.agent import agents

    async def fake_team_prompt():
        return "## Agent Team\n\nteam body\n"

    async def fake_memory_prompt(user_id, query="", workspace_path=""):
        return f"## Team Knowledge\n\nmemory for {user_id}"

    async def no_project_memory(user_id, workspace):
        return None

    monkeypatch.setattr(agents, "build_team_prompt", fake_team_prompt)
    monkeypatch.setattr(agents, "build_memory_prompt", fake_memory_prompt)
    monkeypatch.setattr(project_memory, "load_project_memory", no_project_memory)
    (tmp_path / "AGENTS.md").write_text("Run `make test` before committing.", encoding="utf-8")

    prompt = await sp.build_system_prompt(tmp_path, user_id=7, interface="terminal")

    positions = [
        prompt.index("You are CrabAgent"),
        prompt.index("## Agent Team"),
        prompt.index("memory for 7"),
        prompt.index(sp.AGENTS_MD_SECTION_TITLE),
        prompt.index("Run `make test` before committing."),
    ]
    assert positions == sorted(positions)
    assert "\n\n\n" not in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_survives_failing_sections(tmp_path, monkeypatch):
    from crabagent.core.agent import agents

    async def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    from crabagent.core import project_memory

    monkeypatch.setattr(agents, "build_team_prompt", broken)
    monkeypatch.setattr(agents, "build_memory_prompt", broken)
    monkeypatch.setattr(project_memory, "load_project_memory", broken)

    prompt = await sp.build_system_prompt(tmp_path, user_id=0)

    assert prompt.startswith(sp.render_base_prompt(tmp_path).split("Today is")[0])
    assert "task_add" in prompt


def test_compacted_summary_is_framed_as_history_for_the_model():
    context = AgentContext(workspace=Path.cwd(), system_prompt="sys")
    context.messages = [
        {"role": "compress", "content": "## Requests and intent\nShip the release."},
        {"role": "assistant", "content": "Understood.", "_compress_ack": True},
    ]

    messages = _build_messages(context)

    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == SUMMARY_PREFIX + "## Requests and intent\nShip the release."


def test_base_prompt_mentions_tool_search_only_while_tools_are_deferred(tmp_path, monkeypatch):
    from crabagent.core.config import settings

    monkeypatch.setattr(settings, "defer_tools", True)
    assert "tool_search" in sp.render_base_prompt(tmp_path)

    monkeypatch.setattr(settings, "defer_tools", False)
    assert "tool_search" not in sp.render_base_prompt(tmp_path)

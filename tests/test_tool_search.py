"""Tests for on-demand tool loading (core/agent/tools/tool_search.py)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import crabagent.core.agent.loop as loop
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools import tool_search as ts
from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.config import settings
from tests.test_agent_loop import _async_return, _chunk, _delta, _stream, _tool_call, _usage

CORE = ["read", "bash", "web_search"]
OFFICE = ["office_create", "office_edit"]
EMAIL = ["email_send", "email_check"]
EXTRA = ["mcp__github__create_issue", "my_custom_tool"]


def _registry(names: list[str]) -> ToolRegistry:
    reg = ToolRegistry()
    for name in names:
        reg.register(
            name,
            f"{name} does something useful. More detail follows.",
            {"type": "object", "properties": {"value": {"type": "string"}}},
        )(lambda value="", context=None, _name=name: f"ran {_name}")
    reg._tools[ts.TOOL_SEARCH_NAME] = ts.registry.get(ts.TOOL_SEARCH_NAME)
    return reg


def _context(names: list[str] | None = None, tmp_path: Path | None = None) -> AgentContext:
    tool_names = names if names is not None else CORE + OFFICE + EMAIL + ["image_generate"] + EXTRA
    return AgentContext(workspace=tmp_path or Path.cwd(), tool_registry=_registry(tool_names))


def _advertised(context: AgentContext) -> set[str]:
    excluded = ts.deferred_exclusions(context)
    return {tool["function"]["name"] for tool in context.tool_registry.tool_defs(exclude_names=excluded)}


@pytest.fixture(autouse=True)
def _deferral_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "defer_tools", True)


# ── what is advertised ────────────────────────────────────────────────


def test_only_core_tools_and_tool_search_are_advertised_at_first():
    context = _context()
    context.messages = [{"role": "user", "content": "refactor the parser"}]

    names = _advertised(context)

    assert names == set(CORE) | set(EXTRA) | {ts.TOOL_SEARCH_NAME}


def test_custom_and_mcp_tools_are_never_deferred():
    context = _context()

    assert set(EXTRA) <= _advertised(context)


def test_everything_is_advertised_when_deferral_is_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "defer_tools", False)
    context = _context()

    names = _advertised(context)

    assert set(OFFICE) | set(EMAIL) <= names
    assert ts.TOOL_SEARCH_NAME not in names
    assert ts.intercept_unloaded_call(context, "office_create") is None


def test_tool_search_is_hidden_when_nothing_can_be_loaded():
    context = _context(CORE)

    assert ts.TOOL_SEARCH_NAME not in _advertised(context)


# ── automatic loading from the conversation ───────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Put the Q3 numbers into an Excel spreadsheet", "office"),
        ("please summarise report.DOCX for me", "office"),
        ("Email the summary to the team", "email"),
        ("check my inbox", "email"),
    ],
)
def test_keywords_in_a_user_message_load_the_group(message: str, expected: str):
    context = _context()
    context.messages = [{"role": "user", "content": message}]

    ts.deferred_exclusions(context)

    assert expected in ts.loaded_groups(context)


def test_keywords_match_whole_words_only():
    context = _context()
    context.messages = [{"role": "user", "content": "the mailman emailed nobody; fix the excellent wordplay"}]

    ts.deferred_exclusions(context)

    assert ts.loaded_groups(context) == set()


def test_attached_image_loads_the_image_tools():
    context = _context()
    context.messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "make this brighter"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
            ],
        }
    ]

    assert "image_generate" in _advertised(context)


def test_message_naming_a_tool_loads_its_group():
    context = _context()
    context.messages = [
        {"role": "workspace", "content": "The user is editing this Office document. Use the office_edit tool."}
    ]

    assert set(OFFICE) <= _advertised(context)


def test_loaded_skill_that_mentions_tools_loads_their_group():
    context = _context()
    context.messages = [
        {"role": "user", "content": "write up the findings"},
        {"role": "tool", "name": "skill", "content": "# Skill: docx\nCreate the file with `office_create`."},
        {"role": "tool", "name": "read", "content": "source code that mentions email_send"},
    ]

    ts.deferred_exclusions(context)

    assert ts.loaded_groups(context) == {"office"}


def test_history_restores_groups_that_were_used_or_searched_for():
    context = _context()
    context.messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "1", "function": {"name": "office_create", "arguments": "{}"}},
                {"id": "2", "function": {"name": ts.TOOL_SEARCH_NAME, "arguments": json.dumps({"query": "email"})}},
            ],
        },
    ]

    ts.deferred_exclusions(context)

    assert ts.loaded_groups(context) == {"office", "email"}


def test_new_messages_are_picked_up_and_compaction_triggers_a_rescan():
    context = _context()
    context.messages = [{"role": "user", "content": "hello"}]
    assert not (set(OFFICE) & _advertised(context))

    context.messages.append({"role": "user", "content": "now build the slides"})
    assert set(OFFICE) <= _advertised(context)

    # Compression swaps in a new, shorter list; groups already loaded stay loaded.
    context.messages = [{"role": "compress", "content": "Summary: the user asked to send an email."}]
    names = _advertised(context)
    assert set(OFFICE) <= names and set(EMAIL) <= names


# ── the tool_search tool ──────────────────────────────────────────────


def test_tool_search_loads_a_group_by_name_and_describes_its_tools():
    context = _context()

    result = ts.tool_search("office", context=context)

    assert "Loaded `office`" in result
    assert "- office_create: office_create does something useful." in result
    assert "next step" in result
    assert set(OFFICE) <= _advertised(context)


def test_tool_search_accepts_a_tool_name_keywords_and_several_groups():
    context = _context()

    assert "Loaded `email`" in ts.tool_search("email_send", context=context)
    assert "Already loaded `email`" in ts.tool_search("send a newsletter", context=context)
    both = ts.tool_search("office, images", context=_context())
    assert "Loaded `office`" in both and "Loaded `images`" in both


def test_tool_search_lists_groups_and_reports_no_match():
    context = _context()

    listing = ts.tool_search("list", context=context)
    assert "- office: " in listing and "- email: " in listing
    assert "calendar" not in listing  # not registered in this context

    assert "No tool group matches 'quantum teleporter'" in ts.tool_search("quantum teleporter", context=context)
    assert ts.loaded_groups(context) == set()


def test_tool_search_never_offers_tools_the_agent_is_denied():
    context = _context()
    context.tool_permissions = {"email_send": "deny", "email_check": "deny"}

    assert "No tool group matches" in ts.tool_search("email", context=context)
    assert "email" not in ts.tool_search("list", context=context)


# ── direct calls to tools that were not advertised ────────────────────


def test_unloaded_tool_call_is_held_back_and_its_group_loaded():
    context = _context()
    ts.deferred_exclusions(context)

    notice = ts.intercept_unloaded_call(context, "office_create")

    assert notice is not None and "did not run" in notice and "`office`" in notice
    assert "office" in ts.loaded_groups(context)
    # Core tools, unknown tools and tools advertised in this step run normally.
    assert ts.intercept_unloaded_call(context, "read") is None
    assert ts.intercept_unloaded_call(context, "no_such_tool") is None
    ts.deferred_exclusions(context)
    assert ts.intercept_unloaded_call(context, "office_create") is None


# ── through the agent loop ────────────────────────────────────────────


def _patch_model(monkeypatch: pytest.MonkeyPatch, responses: list[list], seen_tools: list[set[str]]):
    async def fake_provider(provider_name=None):
        return SimpleNamespace(name="mock", provider_type="openai", api_key="k", base_url="", enabled=True)

    async def fake_acompletion(**kwargs):
        seen_tools.append({tool["function"]["name"] for tool in kwargs.get("tools") or []})
        return _stream(responses[len(seen_tools) - 1])

    monkeypatch.setattr(loop, "_resolve_provider", fake_provider)
    monkeypatch.setattr(
        loop, "litellm", SimpleNamespace(acompletion=fake_acompletion, exceptions=loop.litellm.exceptions)
    )
    monkeypatch.setattr("crabagent.core.proxy.resolve_llm_proxy", _async_return(""))


@pytest.mark.asyncio
async def test_loop_adds_a_group_to_the_request_after_tool_search(monkeypatch: pytest.MonkeyPatch):
    context = _context()
    context.model = "gpt-4o"
    context.max_iterations = 4
    seen: list[set[str]] = []
    _patch_model(
        monkeypatch,
        [
            [_chunk(delta=_delta(tool_calls=[_tool_call("c1", ts.TOOL_SEARCH_NAME, '{"query": "office"}')]))],
            [_chunk(delta=_delta(tool_calls=[_tool_call("c2", "office_create", '{"value": "x"}')]))],
            [_chunk(delta=_delta(content="done"), finish_reason="stop", usage=_usage())],
        ],
        seen,
    )

    messages = await loop.run_agent(context, "prepare the quarterly numbers")

    assert not (set(OFFICE) & seen[0]) and ts.TOOL_SEARCH_NAME in seen[0]
    assert set(OFFICE) <= seen[1]
    tool_results = [m["content"] for m in messages if m.get("role") == "tool"]
    assert "Loaded `office`" in tool_results[0]
    assert tool_results[1] == "ran office_create"


@pytest.mark.asyncio
async def test_loop_holds_back_a_direct_call_to_an_unloaded_tool(monkeypatch: pytest.MonkeyPatch):
    context = _context()
    context.model = "gpt-4o"
    context.max_iterations = 4
    seen: list[set[str]] = []
    _patch_model(
        monkeypatch,
        [
            [_chunk(delta=_delta(tool_calls=[_tool_call("c1", "email_send", '{"value": "guessed"}')]))],
            [_chunk(delta=_delta(tool_calls=[_tool_call("c2", "email_send", '{"value": "real"}')]))],
            [_chunk(delta=_delta(content="done"), finish_reason="stop", usage=_usage())],
        ],
        seen,
    )

    messages = await loop.run_agent(context, "let the team know")

    assert "email_send" not in seen[0]
    assert "email_send" in seen[1]
    tool_results = [m["content"] for m in messages if m.get("role") == "tool"]
    assert "did not run" in tool_results[0]
    assert tool_results[1] == "ran email_send"


def test_group_definitions_are_consistent():
    all_tools = [tool for group in ts.GROUPS for tool in group.tools]

    assert len(all_tools) == len(set(all_tools)), "a tool is listed in two groups"
    assert ts.TOOL_SEARCH_NAME not in all_tools
    # Tools the base prompt, team prompt and execution contract rely on stay loaded.
    always_loaded = {
        "read", "write", "edit", "glob", "grep", "bash", "bash_output", "bash_stop", "web_search",
        "web_scrape", "skill", "ask_question", "todo_add", "task_add", "task_update", "molt_rollback",
        "delegate_task", "delegate_parallel", "run_pipeline", "plan_task", "handoff_to", "list_agents",
        "shared_put", "memory_save", "memory_recall", "update_agents_md", "computer_observe", "computer_act",
    }
    assert not (always_loaded & set(all_tools))
    for group in ts.GROUPS:
        assert group.name in ts._DESCRIPTION


# ── agent profiles ────────────────────────────────────────────────────


def test_sub_agent_profile_text_loads_the_tools_its_role_needs():
    context = _context(CORE + OFFICE + ["create_tool", "update_tool", "conversation_search"])
    context.metadata["_agent_profile_text"] = (
        "Systems Administrator & Tool Developer\nCreate, update, and delete reusable tools for the agent team."
    )
    context.messages = [{"role": "user", "content": "## Your Task\nAdd a CSV parser."}]

    names = _advertised(context)

    assert {"create_tool", "update_tool"} <= names
    assert not (set(OFFICE) & names)


def test_agent_switch_note_loads_from_the_profile_but_not_from_boilerplate():
    from crabagent.core.agent import agents

    profile = {
        "name": "optimizer",
        "display_name": "Prompt Engineer",
        "role": "AI Workflow Optimization Specialist",
        "goal": "Analyze past conversations and propose prompt updates.",
        "backstory": "",
        "icon": "",
    }
    switch = agents.build_agent_switch_msg(profile, extra_lines=[agents._CAPABILITY_BROWSER])
    context = _context(CORE + ["conversation_search", "browser_navigate", "collab_browser_open"])
    context.messages = [switch, {"role": "user", "content": "start"}]

    ts.deferred_exclusions(context)

    # "past conversations" is in the goal; the browser capability hint is shared boilerplate.
    assert ts.loaded_groups(context) == {"history"}

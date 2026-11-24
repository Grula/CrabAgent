"""Regression tests for agent-profile tool narrowing.

Two bugs are covered:

* ``filter_tool_registry`` used to strip delegation tools from the *main* agent,
  so switching to any non-default profile silently removed ``delegate_task``,
  ``list_agents``, ``handoff_to`` and ``ask_question``.
* ``spawn_sub_agent`` derived its tool pool from the parent's already-narrowed
  registry, so a parent profile's ``deny`` list leaked into every delegated
  sub-agent (e.g. coder denies ``browser`` -> researcher never got it either).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import crabagent.core.agent.agents as agents_module
from crabagent.core.agent.agent_switch import filter_tool_registry
from crabagent.core.agent.agents import get_delegation_tools
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.registry import ToolRegistry

ALL_TOOLS = [
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "web_search",
    "web_scrape",
    "browser",
    "shared_get",
    "shared_put",
    "shared_list",
    "delegate_task",
    "delegate_parallel",
    "run_pipeline",
    "plan_task",
    "list_agents",
    "handoff_to",
    "switch_agent",
    "ask_question",
    "request_help",
    "create_tool",
]


def _registry(names: list[str] | None = None) -> ToolRegistry:
    reg = ToolRegistry()
    for name in names or ALL_TOOLS:
        reg.register(name, f"{name} tool", {})(lambda **kwargs: None)
    return reg


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


class TestFilterToolRegistry:
    def test_sub_agent_scope_still_blocks_delegation(self):
        """Default behaviour (sub-agent) must not change: no delegation plumbing."""
        filtered = filter_tool_registry(_registry())
        assert not (get_delegation_tools() & set(filtered._tools))
        assert "create_tool" not in filtered._tools

    def test_main_agent_keeps_delegation_tools(self):
        """Regression: the main agent must keep the ability to delegate and ask."""
        filtered = filter_tool_registry(_registry(), allow_delegation=True)
        names = set(filtered._tools)
        assert get_delegation_tools() <= names
        assert "create_tool" in names

    def test_main_agent_keeps_delegation_while_honouring_deny(self):
        filtered = filter_tool_registry(
            _registry(),
            tool_permissions={"browser": "deny", "bash": "auto"},
            allow_delegation=True,
        )
        names = set(filtered._tools)
        assert "delegate_task" in names
        assert "ask_question" in names
        assert "browser" not in names
        assert "bash" in names

    def test_source_registry_is_not_mutated(self):
        source = _registry()
        before = set(source._tools)
        filter_tool_registry(source, tool_permissions={"browser": "deny"}, allow_delegation=True)
        assert set(source._tools) == before


class TestSubAgentInheritsUnprunedPool:
    @pytest.mark.asyncio
    async def test_sub_agent_gets_tool_denied_by_parent_profile(self, monkeypatch: pytest.MonkeyPatch):
        researcher = {
            "name": "researcher",
            "display_name": "Web Researcher",
            "role": "Research",
            "goal": "Find facts",
            "backstory": "",
            "model": "",
            "allow_delegation": False,
            # researcher itself does not deny browser
            "tool_permissions": {"bash": "deny", "write": "deny", "edit": "deny"},
        }
        monkeypatch.setattr(agents_module, "get_agent", _async_return(researcher))
        monkeypatch.setattr(agents_module, "_classify_task", lambda task: "research")
        monkeypatch.setattr(agents_module, "inject_agent_lessons", _async_return("prompt"))
        monkeypatch.setattr(agents_module, "_load_shared_context", _async_return(""))

        captured: dict[str, set[str]] = {}

        async def fake_run_agent(context, query):
            captured["tools"] = set(context.tool_registry._tools)
            context.messages.append({"role": "assistant", "content": "done"})
            return context.messages

        monkeypatch.setattr("crabagent.core.agent.loop.run_agent", fake_run_agent)

        full = _registry()
        # Parent (coder) narrowed its own view: browser denied by coder's profile.
        narrowed = filter_tool_registry(full, tool_permissions={"browser": "deny"}, allow_delegation=True)
        parent = AgentContext(workspace=Path.cwd(), tool_registry=narrowed)
        parent.base_tool_registry = full
        assert "browser" not in parent.tool_registry._tools

        await agents_module.spawn_sub_agent("researcher", "look at the page", parent)

        assert "browser" in captured["tools"], "parent deny leaked into sub-agent"
        # Sub-agent still honours its OWN deny list.
        assert "bash" not in captured["tools"]
        assert "write" not in captured["tools"]

    @pytest.mark.asyncio
    async def test_falls_back_to_parent_registry_without_base(self, monkeypatch: pytest.MonkeyPatch):
        """When no base pool is set, behaviour must stay backward compatible."""
        coder = {
            "name": "coder",
            "display_name": "Code Expert",
            "role": "Code",
            "goal": "Ship",
            "backstory": "",
            "model": "",
            "allow_delegation": False,
            "tool_permissions": {},
        }
        monkeypatch.setattr(agents_module, "get_agent", _async_return(coder))
        monkeypatch.setattr(agents_module, "_classify_task", lambda task: "code")
        monkeypatch.setattr(agents_module, "inject_agent_lessons", _async_return("prompt"))
        monkeypatch.setattr(agents_module, "_load_shared_context", _async_return(""))

        captured: dict[str, set[str]] = {}

        async def fake_run_agent(context, query):
            captured["tools"] = set(context.tool_registry._tools)
            context.messages.append({"role": "assistant", "content": "done"})
            return context.messages

        monkeypatch.setattr("crabagent.core.agent.loop.run_agent", fake_run_agent)

        parent = AgentContext(workspace=Path.cwd(), tool_registry=_registry(["bash", "read"]))
        await agents_module.spawn_sub_agent("coder", "do it", parent)

        assert captured["tools"] == {"bash", "read"}


# ── "confirm" permissions and the "browser" family key ────────────────


def _patch_spawn(monkeypatch: pytest.MonkeyPatch, profile: dict, run_agent):
    monkeypatch.setattr(agents_module, "get_agent", _async_return(profile))
    monkeypatch.setattr(agents_module, "_classify_task", lambda task: "code")
    monkeypatch.setattr(agents_module, "inject_agent_lessons", _async_return("prompt"))
    monkeypatch.setattr(agents_module, "_load_shared_context", _async_return(""))
    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", run_agent)


def _profile(name: str, permissions: dict[str, str]) -> dict:
    return {
        "name": name,
        "display_name": name.title(),
        "role": "Engineer",
        "goal": "Do the task",
        "backstory": "",
        "model": "",
        "allow_delegation": False,
        "tool_permissions": permissions,
    }


class TestSubAgentConfirmation:
    @pytest.mark.asyncio
    async def test_confirm_tool_asks_through_the_parent_and_approval_is_shared(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        asked: list[tuple[str, dict]] = []

        async def parent_confirm(tool_name: str, args: dict) -> bool:
            asked.append((tool_name, args))
            return True

        results: list[object] = []

        async def fake_run_agent(context, query):
            results.append(await context.tool_registry.execute("bash", {"command": "ls"}, context=context))
            results.append(await context.tool_registry.execute("bash", {"command": "pwd"}, context=context))
            context.messages.append({"role": "assistant", "content": "done"})
            return context.messages

        _patch_spawn(monkeypatch, _profile("implementer", {"bash": "confirm"}), fake_run_agent)
        parent = AgentContext(workspace=Path.cwd(), tool_registry=_registry(), confirm_callback=parent_confirm)

        await agents_module.spawn_sub_agent("implementer", "run it", parent)

        # Asked once, with the asking agent named; the second call reuses the approval.
        assert asked == [("bash", {"agent": "implementer", "command": "ls"})]
        assert "bash" in parent.approved_tools
        assert all("denied" not in str(result) for result in results)

    @pytest.mark.asyncio
    async def test_denied_confirmation_stops_the_tool(self, monkeypatch: pytest.MonkeyPatch):
        async def parent_confirm(tool_name: str, args: dict) -> bool:
            return False

        results: list[object] = []

        async def fake_run_agent(context, query):
            results.append(await context.tool_registry.execute("bash", {"command": "ls"}, context=context))
            context.messages.append({"role": "assistant", "content": "done"})
            return context.messages

        _patch_spawn(monkeypatch, _profile("implementer", {"bash": "confirm"}), fake_run_agent)
        parent = AgentContext(workspace=Path.cwd(), tool_registry=_registry(), confirm_callback=parent_confirm)

        await agents_module.spawn_sub_agent("implementer", "run it", parent)

        assert results == ["Tool 'bash' execution denied by user."]
        assert "bash" not in parent.approved_tools

    @pytest.mark.asyncio
    async def test_unattended_parent_still_runs_confirm_tools_without_asking(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict[str, object] = {}

        async def fake_run_agent(context, query):
            captured["callback"] = context.confirm_callback
            captured["result"] = await context.tool_registry.execute("bash", {"command": "ls"}, context=context)
            context.messages.append({"role": "assistant", "content": "done"})
            return context.messages

        _patch_spawn(monkeypatch, _profile("implementer", {"bash": "confirm"}), fake_run_agent)
        parent = AgentContext(workspace=Path.cwd(), tool_registry=_registry())  # e.g. a scheduled task

        await agents_module.spawn_sub_agent("implementer", "run it", parent)

        assert captured["callback"] is None
        assert "denied" not in str(captured["result"])


class TestBrowserFamilyPermission:
    def test_browser_key_covers_the_browser_tools_but_exact_entries_win(self):
        from crabagent.core.agent.tools.registry import resolve_permission

        permissions = {"browser": "deny", "browser_screenshot": "auto"}

        assert resolve_permission(permissions, "browser_navigate") == "deny"
        assert resolve_permission(permissions, "browser_screenshot") == "auto"
        assert resolve_permission(permissions, "collab_browser_open") is None
        assert resolve_permission(permissions, "bash") is None
        assert resolve_permission({}, "browser_navigate") is None

    def test_denying_browser_removes_the_browser_tools_from_an_agent(self):
        full = _registry([*ALL_TOOLS, "browser_navigate", "browser_click", "collab_browser_open"])

        filtered = filter_tool_registry(full, tool_permissions={"browser": "deny"}, allow_delegation=True)

        assert "browser_navigate" not in filtered._tools and "browser_click" not in filtered._tools
        assert "collab_browser_open" in filtered._tools and "bash" in filtered._tools

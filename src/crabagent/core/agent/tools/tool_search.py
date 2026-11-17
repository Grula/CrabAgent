"""On-demand tool loading.

Sending every tool definition with every request costs tens of thousands of
tokens. With ``settings.defer_tools`` on, only the core tools are advertised
to the model; the groups listed in ``GROUPS`` are left out until they are
needed. A group is loaded when:

* a message in the conversation contains one of its keywords, names one of
  its tools, or has an image attached (``images`` group);
* a skill that was loaded mentions one of its tools;
* the conversation history shows the group was used or searched for before;
* the model asks for it with the ``tool_search`` tool;
* the model calls one of its tools directly. That call is not executed (the
  model never saw the parameters); the group is loaded and the model retries.

Only tools named in ``GROUPS`` are ever deferred. Custom tools, MCP tools and
anything else stay loaded, so a tool is deferred on purpose, never by accident.

The loaded set is rebuilt from the conversation itself, so it is the same on
every run of a conversation and does not need to be stored anywhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from crabagent.core.agent.tools.registry import registry, resolve_permission

TOOL_SEARCH_NAME = "tool_search"

_LOADED_KEY = "_loaded_tool_groups"
_SCAN_KEY = "_tool_group_scan"
_HIDDEN_KEY = "_tools_hidden_this_step"


@dataclass(frozen=True)
class ToolGroup:
    name: str
    summary: str
    tools: tuple[str, ...]
    # Words or phrases that load the group when they appear in a message.
    keywords: tuple[str, ...] = ()


GROUPS: tuple[ToolGroup, ...] = (
    ToolGroup(
        name="office",
        summary="create, read and edit Word, Excel and PowerPoint files",
        tools=(
            "office_create", "office_read", "office_edit", "office_batch_edit",
            "office_query", "office_render", "office_help",
        ),
        keywords=(
            "word document", "word doc", "word file", "ms word", "microsoft word", "docx",
            "excel", "xlsx", "xls", "spreadsheet", "workbook",
            "powerpoint", "pptx", "ppt", "slide deck", "slides", "slide", "presentation",
            "office document",
        ),
    ),
    ToolGroup(
        name="browser",
        summary="drive a headless browser: open pages, click, type, scroll, extract, screenshot",
        tools=(
            "browser_navigate", "browser_click", "browser_click_index", "browser_type",
            "browser_scroll", "browser_extract", "browser_screenshot", "browser_close",
        ),
        keywords=(
            "browser", "click", "screenshot", "log in", "login", "sign in", "sign up",
            "web app", "localhost", "fill in", "fill out", "playwright",
        ),
    ),
    ToolGroup(
        name="shared_browser",
        summary="operate the visible browser the user can watch and take over",
        tools=(
            "collab_browser_open", "collab_browser_history", "collab_browser_observe", "collab_browser_click",
            "collab_browser_type", "collab_browser_press_key", "collab_browser_scroll",
            "collab_browser_select", "collab_browser_wait_for", "collab_browser_wait_for_user",
            "collab_browser_screenshot", "collab_browser_authorize_local_preview",
        ),
        keywords=("browser", "shared browser", "visible browser", "take over", "local preview"),
    ),
    ToolGroup(
        name="images",
        summary="generate new images and edit existing ones",
        tools=("image_generate", "image_edit"),
        keywords=(
            "image", "images", "picture", "photo", "logo", "illustration", "icon", "draw",
            "drawing", "poster", "banner", "wallpaper", "avatar", "sticker",
        ),
    ),
    ToolGroup(
        name="email",
        summary="read, draft and send email, daily digests and task reminders",
        tools=(
            "email_send", "email_check", "email_send_draft", "email_reply_draft",
            "daily_digest", "task_remind",
        ),
        keywords=("email", "emails", "e-mail", "mail", "inbox", "mailbox", "digest", "newsletter"),
    ),
    ToolGroup(
        name="calendar",
        summary="list, add, change and delete calendar events",
        tools=("calendar_add", "calendar_list", "calendar_today", "calendar_update", "calendar_delete"),
        keywords=(
            "calendar", "appointment", "meeting", "meetings", "agenda", "availability", "reschedule",
        ),
    ),
    ToolGroup(
        name="meetings",
        summary="turn meeting notes into action items",
        tools=("meeting_process",),
        keywords=("meeting notes", "meeting minutes", "transcript", "action items"),
    ),
    ToolGroup(
        name="schedules",
        summary="create and manage tasks that run on a schedule",
        tools=(
            "scheduled_task_create", "scheduled_task_list", "scheduled_task_get",
            "scheduled_task_update", "scheduled_task_delete", "scheduled_task_pause",
            "scheduled_task_resume",
        ),
        keywords=(
            "schedule", "scheduled", "scheduling", "every day", "every morning", "every evening",
            "every night", "every week", "every month", "every hour", "daily", "weekly", "hourly",
            "monthly", "cron", "remind me", "reminder", "recurring", "periodically",
        ),
    ),
    ToolGroup(
        name="background_agents",
        summary="run sub-agents in the background and collect their results later",
        tools=(
            "delegate_background", "background_list", "background_status", "background_wait",
            "background_cancel", "background_group_start", "background_group_status",
            "background_group_wait", "background_group_cancel",
        ),
        keywords=("background agent", "background agents", "background group"),
    ),
    ToolGroup(
        name="memory_admin",
        summary="list, replace and delete saved memories",
        tools=("memory_replace", "memory_list", "memory_forget"),
        keywords=("memory", "memories", "forget", "what do you remember", "what do you know about me"),
    ),
    ToolGroup(
        name="custom_tools",
        summary="create, update and delete reusable custom tools",
        tools=("create_tool", "update_tool", "delete_tool"),
        keywords=(
            "custom tool", "custom tools", "create a tool", "new tool", "make a tool",
            "build a tool", "update the tool", "delete the tool", "reusable tool", "reusable tools",
            "tool developer", "toolsmith",
        ),
    ),
    ToolGroup(
        name="history",
        summary="search earlier conversations and inspect past agent runs",
        tools=("conversation_search", "agent_runs_inspect"),
        keywords=(
            "previous conversation", "previous conversations", "earlier conversation",
            "past conversation", "past conversations", "last conversation", "other conversation",
            "conversation history", "chat history", "we discussed", "we talked about", "last time",
            "previous session", "agent run", "agent runs",
        ),
    ),
)

_GROUP_BY_NAME: dict[str, ToolGroup] = {group.name: group for group in GROUPS}
_GROUP_OF_TOOL: dict[str, ToolGroup] = {tool: group for group in GROUPS for tool in group.tools}


def _alternation(words: tuple[str, ...] | list[str], extra_boundary: str = "") -> re.Pattern[str]:
    """Match any of *words* as whole words or phrases, ignoring case."""
    boundary = f"a-z0-9{extra_boundary}"
    body = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(f"(?<![{boundary}])(?:{body})(?![{boundary}])", re.IGNORECASE)


_KEYWORD_PATTERNS: dict[str, re.Pattern[str]] = {
    group.name: _alternation(group.keywords) for group in GROUPS if group.keywords
}
_TOOL_NAME_PATTERN = _alternation(list(_GROUP_OF_TOOL), extra_boundary="_")


def deferral_enabled() -> bool:
    from crabagent.core.config import settings

    return bool(getattr(settings, "defer_tools", True))


# ── Which groups can this context use, and which are loaded ────────────


def _available_groups(context: Any) -> dict[str, list[str]]:
    """Map each group to the tools in it that this context may call."""
    tool_registry = getattr(context, "tool_registry", None)
    if tool_registry is None:
        return {}
    permissions = getattr(context, "tool_permissions", None) or {}
    available: dict[str, list[str]] = {}
    for group in GROUPS:
        names = [
            name
            for name in group.tools
            if tool_registry.get(name) is not None and resolve_permission(permissions, name) != "deny"
        ]
        if names:
            available[group.name] = names
    return available


def loaded_groups(context: Any) -> set[str]:
    return context.metadata.setdefault(_LOADED_KEY, set())


def load_group(context: Any, name: str) -> bool:
    """Mark *name* as loaded. Returns True when it was not loaded before."""
    if name not in _GROUP_BY_NAME:
        return False
    loaded = loaded_groups(context)
    if name in loaded:
        return False
    loaded.add(name)
    return True


# ── Loading groups from what the conversation says ─────────────────────


def _text_and_images(content: Any) -> tuple[str, bool]:
    """Return the text of a message and whether an image is attached to it."""
    if isinstance(content, str):
        return content, False
    if not isinstance(content, list):
        return "", False
    parts: list[str] = []
    has_image = False
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") in ("image_url", "image"):
                has_image = True
    return " ".join(parts), has_image


def _groups_named_in(text: str) -> set[str]:
    """Groups whose tools are mentioned by exact name in *text*."""
    return {_GROUP_OF_TOOL[match.group(0).lower()].name for match in _TOOL_NAME_PATTERN.finditer(text)}


def _groups_in_text(text: str) -> set[str]:
    """Groups that *text* calls for, by tool name or by keyword."""
    found = _groups_named_in(text)
    for group_name, pattern in _KEYWORD_PATTERNS.items():
        if pattern.search(text):
            found.add(group_name)
    return found


_AGENT_SWITCH_PREFIX = "[Agent Switch]"


def _profile_part_of_switch_message(text: str) -> str:
    """Keep the agent's own role, goal and background from an agent-switch note.

    The rest of the note is boilerplate shared by every agent (capability
    hints, working rules); scanning it would load tools nobody asked for.
    """
    from crabagent.core.agent.agents import _CAPABILITY_BROWSER

    return text.split("Working rules:", 1)[0].replace(_CAPABILITY_BROWSER, "")


def _groups_for_message(message: dict) -> set[str]:
    role = message.get("role", "")
    found: set[str] = set()

    if role == "assistant":
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            name = function.get("name", "")
            if name in _GROUP_OF_TOOL:
                found.add(_GROUP_OF_TOOL[name].name)
            elif name == TOOL_SEARCH_NAME:
                # Replay earlier searches so a continued conversation keeps its tools.
                try:
                    query = json.loads(function.get("arguments") or "{}").get("query", "")
                except (ValueError, AttributeError):
                    query = ""
                found.update(_match_groups(str(query), set(_GROUP_BY_NAME)))
        return found

    text, has_image = _text_and_images(message.get("content"))

    if role == "tool":
        # A loaded skill is a playbook: load the tools it tells the agent to use.
        if message.get("name") == "skill" and text:
            found.update(_groups_named_in(text))
        return found

    if role == "system":
        return found

    # User messages and the notes shown to the model as user turns
    # (workspace switch, agent switch, context summary).
    if has_image:
        found.add("images")
    if text.startswith(_AGENT_SWITCH_PREFIX):
        text = _profile_part_of_switch_message(text)
    if text:
        found.update(_groups_in_text(text))
    return found


def sync_loaded_groups(context: Any) -> None:
    """Scan messages not seen yet and load the groups they call for."""
    messages = getattr(context, "messages", None) or []
    state = context.metadata.get(_SCAN_KEY)
    start = 0
    # Context compression replaces the list; start over when that happens.
    if state and state.get("list_id") == id(messages) and state.get("position", 0) <= len(messages):
        start = state["position"]
    if state is None:
        # A sub-agent's role, goal and backstory say what it is for.
        for group_name in _groups_in_text(str(context.metadata.get("_agent_profile_text") or "")):
            load_group(context, group_name)
    for message in messages[start:]:
        if isinstance(message, dict):
            for group_name in _groups_for_message(message):
                load_group(context, group_name)
    context.metadata[_SCAN_KEY] = {"list_id": id(messages), "position": len(messages)}


# ── Hooks used by the agent loop ───────────────────────────────────────


def deferred_exclusions(context: Any) -> set[str]:
    """Tool names to leave out of the next model request."""
    if not deferral_enabled():
        context.metadata[_HIDDEN_KEY] = set()
        return {TOOL_SEARCH_NAME}

    sync_loaded_groups(context)
    loaded = loaded_groups(context)
    hidden: set[str] = set()
    for group in GROUPS:
        if group.name not in loaded:
            hidden.update(group.tools)
    context.metadata[_HIDDEN_KEY] = set(hidden)
    # Nothing this context could ever load: don't advertise the search tool.
    if not _available_groups(context):
        hidden.add(TOOL_SEARCH_NAME)
    return hidden


def intercept_unloaded_call(context: Any, tool_name: str) -> str | None:
    """Handle a call to a tool the model was not shown in this step.

    The model guessed the tool's name without seeing its parameters, so the
    call is not executed. The tool's group is loaded and the model is asked to
    call again once the definition is in its tool list.
    """
    if tool_name not in context.metadata.get(_HIDDEN_KEY, ()):
        return None
    group = _GROUP_OF_TOOL.get(tool_name)
    if group is None or context.tool_registry.get(tool_name) is None:
        return None
    if resolve_permission(getattr(context, "tool_permissions", None), tool_name) == "deny":
        return None  # let the registry report that the tool is disabled
    load_group(context, group.name)
    return (
        f"The `{tool_name}` tool was not loaded yet, so this call did not run. The `{group.name}` tools are "
        "loaded now: check the tool's parameters in your tool list and call it again."
    )


# ── The tool_search tool ───────────────────────────────────────────────


def _match_groups(query: str, candidates: set[str]) -> list[str]:
    """Resolve *query* to group names, best match first."""
    terms = [term for term in re.split(r"[,;\s]+", query.lower().strip()) if term]
    if not terms:
        return []

    exact: list[str] = []
    for term in terms:
        if term in candidates and term not in exact:
            exact.append(term)
        elif term in _GROUP_OF_TOOL and _GROUP_OF_TOOL[term].name in candidates:
            if _GROUP_OF_TOOL[term].name not in exact:
                exact.append(_GROUP_OF_TOOL[term].name)
    if exact:
        return exact

    scores: dict[str, int] = {}
    for group in GROUPS:
        if group.name not in candidates:
            continue
        descriptions = " ".join(
            tool.description for name in group.tools if (tool := registry.get(name)) is not None
        )
        haystack = " ".join(
            (group.name.replace("_", " "), group.summary, " ".join(group.keywords), " ".join(group.tools), descriptions)
        ).lower()
        score = sum(1 for term in terms if len(term) >= 3 and term in haystack)
        if score:
            scores[group.name] = score
    if not scores:
        return []
    best = max(scores.values())
    return [name for name in (group.name for group in GROUPS) if scores.get(name) == best][:3]


def _first_sentence(description: str, limit: int = 140) -> str:
    text = " ".join((description or "").split())
    end = text.find(". ")
    if 0 < end < limit:
        return text[: end + 1]
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _group_listing(available: dict[str, list[str]], loaded: set[str]) -> str:
    lines = []
    for group in GROUPS:
        if group.name in available:
            status = " (loaded)" if group.name in loaded else ""
            lines.append(f"- {group.name}{status}: {group.summary}")
    return "\n".join(lines)


_DESCRIPTION = (
    "Load tools that are not in your tool list yet. To keep requests small, only the core tools are listed "
    "up front; the groups below are loaded on demand. Call this with a group name, a tool name or a few "
    "keywords, and the tools appear in your tool list on your next step. Search here before concluding that "
    "you cannot do something.\n\nGroups:\n"
    + "\n".join(f"- {group.name}: {group.summary} ({', '.join(group.tools)})" for group in GROUPS)
)


@registry.register(
    name=TOOL_SEARCH_NAME,
    description=_DESCRIPTION,
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    'A group name ("office"), a tool name ("email_send"), or keywords ("send a calendar invite"). '
                    'Several groups can be requested at once, separated by commas. Use "list" to see all groups.'
                ),
            },
        },
        "required": ["query"],
    },
    metadata={"source": "builtin", "category": "tool_management"},
)
def tool_search(query: str = "", context: Any = None) -> str:
    if context is None:
        return "Error: tool_search requires an active session"

    available = _available_groups(context)
    loaded = loaded_groups(context)
    if not available:
        return "There are no additional tools to load for this agent."

    query = (query or "").strip()
    if not query or query.lower() in {"list", "all", "*", "help"}:
        return "Tool groups you can load:\n" + _group_listing(available, loaded)

    matches = _match_groups(query, set(available))
    if not matches:
        return f"No tool group matches '{query}'. Groups you can load:\n" + _group_listing(available, loaded)

    sections: list[str] = []
    for name in matches:
        newly_loaded = load_group(context, name)
        state = "Loaded" if newly_loaded else "Already loaded"
        lines = [f"{state} `{name}`: {_GROUP_BY_NAME[name].summary}"]
        for tool_name in available[name]:
            tool = context.tool_registry.get(tool_name)
            lines.append(f"- {tool_name}: {_first_sentence(tool.description if tool else '')}")
        sections.append("\n".join(lines))
    sections.append("These tools are in your tool list from your next step. Call them directly.")
    return "\n\n".join(sections)

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from crabagent.core.agent.reflect import (
    classify_task as _classify_task,
)
from crabagent.core.agent.reflect import (
    llm_reflect_lesson as _llm_reflect_lesson,
)
from crabagent.core.agent.reflect import (
    rule_extract_lesson as _rule_extract_lesson,
)
from crabagent.core.database import AgentProfile, async_session_factory

logger = logging.getLogger(__name__)

_registry_cache: list[dict] = []
_registry_loaded = False

# ---------------------------------------------------------------------------
# Hardcoded English prompt fragments
#
# These were previously loaded from ``core/i18n/*.json``. The i18n layer has
# been removed: CrabAgent is English-only, so the strings live here as plain
# module constants instead of being looked up by locale at runtime.
# ---------------------------------------------------------------------------

_SUB_AGENT_INTRO = (
    "You are a CrabAgent sub-agent. Another agent delegated a task to you, and your final message "
    "is returned to it as your result, so make that message complete and self-contained."
)
_AGENT_SYSTEM_ROLE = 'Role: {role}'
_AGENT_SYSTEM_GOAL = 'Goal: {goal}'
_AGENT_SYSTEM_BACKSTORY = 'Backstory: {backstory}'
_AGENT_SYSTEM_ENVIRONMENT = 'Today is {date}. Working directory: {workspace}'
_AGENT_EXECUTION_CONTRACT = (
    "Working rules:\n"
    "- You have a limited tool-iteration budget. Work in small steps and verify each one before building on it; "
    "once about 80% of the budget is used, stop starting new work and wrap up.\n"
    "- Confirm your working directory before editing; if it does not match the task, stop and report "
    "instead of guessing.\n"
    "- Follow the project's rules. If they were not given to you, read AGENTS.md in the working directory "
    "(when it exists) before changing files.\n"
    "- Stay within the task. Don't widen the scope, refactor unrelated code, or redo work someone else owns.\n"
    "- Base every claim on what you actually read or ran; never invent file contents, results or sources.\n"
    "- Call tools only through the native tool interface and never print tool-call markup as text.\n"
    "- Don't stop to ask questions: work independently, make reasonable assumptions, and state them in "
    "your report.\n"
    "Finish with a short report:\n"
    "- Outcome in one or two sentences.\n"
    "- Files changed (paths), or key findings with their sources.\n"
    "- Verification: the exact command you ran and its exit code, or why none applied.\n"
    "- Assumptions you made.\n"
    "- An explicit REMAINING list of anything unfinished or blocked (write \"REMAINING: none\" when done)."
)
_AGENT_SYSTEM_SHARED_INSTRUCTION = (
    "Team scratchpad: `shared_put(key, value)` stores a note that agents started later in this session "
    "receive automatically; `shared_get(key)` and `shared_list()` read your teammates' notes. Check it "
    "before repeating work, and before you finish save what others will need under a short key: facts, "
    "paths and decisions, not raw dumps."
)
_AGENT_SYSTEM_HELP_INSTRUCTION = (
    "If part of the task needs expertise you lack, ask a teammate with "
    "`request_help(agent_name, question)` instead of guessing."
)
_AGENT_SWITCH_HEADER = '[Agent Switch] You are now acting as {icon} **{display_name}**.'
_AGENT_SWITCH_ROLE = 'Role: {role}'
_AGENT_SWITCH_GOAL = 'Goal: {goal}'
_AGENT_SWITCH_BACKSTORY = 'Background: {backstory}'
_AGENT_SWITCH_FOOTER = "Your actually available tools are the ones provided in this session's tool list, not the wording of the role description. If a task can be accomplished with tools you actually have, do it directly; do not refuse on role grounds and do not push the work back to the user."
_CAPABILITY_BROWSER = (
    "You can operate the visible shared browser on the user's behalf with computer_observe / computer_act: "
    "observe pages, click, type and navigate websites. High-risk actions automatically ask the user for "
    "confirmation, so never refuse on the user's behalf."
)
_MEMORY_PROMPT_TITLE = '## Team Knowledge'
_MEMORY_PROMPT_SAVE_INSTRUCTION = (
    "These memories were accurate when saved; verify file names, commands and facts before relying on them. "
    "Save new durable knowledge with `memory_save(memory_type='team', ...)`: choices the user made, options "
    "they rejected, preferences, and project facts that will matter in later sessions. Don't save what only "
    "matters to this conversation or what the code already records."
)
_SHARED_CONTEXT_TITLE = '## Shared Team Knowledge'
_TEAM_PROMPT_TITLE = '## Agent Team'
_TEAM_PROMPT_INTRO = (
    'You can delegate work to specialized sub-agents. Use them for parallel work or when a task needs '
    'a specialist; delegation costs time and tokens, so do small or tightly coupled work yourself.'
)
_TEAM_PROMPT_AVAILABLE_AGENTS = '### Available Agents'
_TEAM_PROMPT_DELEGATION_TOOLS = '### Delegation Tools'
_TEAM_PROMPT_SHARED_WORKSPACE = '### Shared Workspace'
_TEAM_PROMPT_WHEN_TO_DELEGATE = '### When to Delegate'
_TEAM_PROMPT_BRIEFING = '### Writing the Task'
_TEAM_PROMPT_RESTRICTED = '(disabled tools: {tools})'
_PIPELINE_GRANULARITY = 'One pipeline step = one file or one concern. If a step has more than ~5 deliverables, split it into separate steps.'

# Delegation-tool bullet lines, in display order.
_TEAM_PROMPT_TOOL_LINES: tuple[str, ...] = (
    '`list_agents`: List all available agents with details',
    '`plan_task(task)`: Analyze a complex task and produce an execution plan (does not execute)',
    '`delegate_task(agent_name, task)`: Delegate a task to a specific agent and wait for its result',
    '`delegate_parallel(tasks)`: Run multiple independent agent tasks simultaneously (no ordering)',
    '`handoff_to(agent_name, context_summary)`: Hand off work to another agent with context',
    '`run_pipeline(steps)`: Multi-step workflow with dependencies. Steps without depends_on run in parallel. Prefer for complex workflows.',
    '`delegate_background(agent_name, task)`: Start a sub-agent in the background and keep working; collect it '
    'with `background_status` / `background_wait`. Background agents get no tools except an optional `read` and '
    'stop after 5 minutes, so use them for drafting or reasoning, not research or edits.',
)

# Shared-workspace bullet lines, in display order.
_TEAM_PROMPT_SHARED_LINES: tuple[str, ...] = (
    '`shared_put(key, value)`: Save findings to shared workspace; agents started later receive them',
    '`shared_get(key)`: Read findings from other agents',
    '`shared_list()`: List all shared notes',
)

# Routing hints for the built-in agents. A hint is shown only while its agent is
# enabled; otherwise the main agent would be sent to an agent that does not exist.
_TEAM_PROMPT_AGENT_ROUTES: tuple[tuple[str, str], ...] = (
    ('researcher', 'Web research, searching, browsing -> `researcher`'),
    ('analyst', 'Data analysis, comparison, report generation -> `analyst`'),
    ('coder', 'Code writing, debugging, refactoring -> `coder`'),
    ('writer', 'Content writing, editing, translation -> `writer`'),
)

# 'When to delegate' bullet lines, in display order.
_TEAM_PROMPT_WHEN_LINES: tuple[str, ...] = (
    'For any other work, pick the agent whose role and goal above fit it best',
    'Multiple independent subtasks (no ordering needed) -> `delegate_parallel`',
    'Multi-step workflow with ordering or data flow (e.g., research -> analyze -> write) -> `run_pipeline`',
    'User asks for multiple sub-agents or parallel execution -> use `delegate_parallel` or `run_pipeline`, not sequential `delegate_task`',
    "Check each agent's disabled tools above before assigning work it cannot do",
    'A lookup you can do in a few tool calls, or a small edit -> do it yourself',
)

# 'Writing the task' bullet lines, in display order.
_TEAM_PROMPT_BRIEFING_LINES: tuple[str, ...] = (
    'Sub-agents do not see this conversation (only `handoff_to` passes a short excerpt). Make every task '
    'self-contained: the goal, relevant file paths or URLs, constraints, what is already ruled out, and '
    'exactly what to return.',
    'Results can be wrong or incomplete. Check important claims and code changes before you rely on them or '
    'pass them on to the user.',
)


async def load_agent_registry() -> list[dict]:
    global _registry_cache, _registry_loaded
    if _registry_loaded:
        return _registry_cache
    async with async_session_factory() as db:
        result = await db.execute(
            select(AgentProfile).where(AgentProfile.enabled.is_(True)).order_by(AgentProfile.name)
        )
        rows = result.scalars().all()
        _registry_cache = []
        for r in rows:
            tools_list = []
            if r.tools:
                try:
                    tools_list = _json.loads(r.tools)
                except Exception:
                    pass
            tool_perms = {}
            if r.tool_permissions:
                try:
                    tool_perms = _json.loads(r.tool_permissions)
                except Exception:
                    pass
            _registry_cache.append(
                {
                    "name": r.name,
                    "display_name": r.display_name or r.name,
                    "role": r.role,
                    "goal": r.goal,
                    "backstory": r.backstory or "",
                    "model": r.model or "",
                    "allow_delegation": r.allow_delegation,
                    "icon": r.icon or "",
                    "is_default": r.is_default,
                    "tools": tools_list,
                    "tool_permissions": tool_perms,
                }
            )
    _registry_loaded = True
    return _registry_cache


def invalidate_cache():
    global _registry_loaded
    _registry_loaded = False


# ── Sub-agent model map (cost control: parent provider|model → child provider|model)
# Loaded once from app_settings and cached; invalidated when settings change.
_sub_agent_model_map_cache: dict[str, str] | None = None


async def load_sub_agent_model_map() -> dict[str, str]:
    """Load the ``sub_agent_model_map`` JSON from app_settings (cached).

    Format: ``{"openai|gpt-4o": "openai|gpt-4o-mini", ...}``.
    Key   = parent agent's ``provider|model``.
    Value = the child agent's ``provider|model`` to switch to.
    """
    global _sub_agent_model_map_cache
    if _sub_agent_model_map_cache is not None:
        return _sub_agent_model_map_cache
    from crabagent.core.database import AppSetting

    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(AppSetting).where(AppSetting.key == "sub_agent_model_map")
            )
            row = result.scalar_one_or_none()
            raw = row.value if row else ""
        data = _json.loads(raw) if raw else {}
        _sub_agent_model_map_cache = data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("Failed to load sub_agent_model_map", exc_info=True)
        _sub_agent_model_map_cache = {}
    return _sub_agent_model_map_cache


def invalidate_sub_agent_model_map() -> None:
    """Drop the cached map so the next delegation reloads it from DB."""
    global _sub_agent_model_map_cache
    _sub_agent_model_map_cache = None


def _resolve_sub_model_provider(
    model_map: dict[str, str],
    parent_model: str,
    parent_provider: str,
    agent_def_model: str,
) -> tuple[str, str]:
    """Resolve the (model, provider) a sub-agent should use.

    Priority:
      1. ``map[parent_provider|parent_model]`` → split into (provider, model).
      2. ``agent_def.model`` (sub-agent's own profile) → provider stays the parent's.
      3. Fallback to the parent's (model, provider).
    """
    parent_key = f"{parent_provider}|{parent_model}"
    mapped = model_map.get(parent_key)
    if mapped and "|" in mapped:
        child_provider, child_model = mapped.split("|", 1)
        if child_provider and child_model:
            return child_model, child_provider
    if agent_def_model:
        return agent_def_model, parent_provider
    return parent_model, parent_provider


async def get_agent(name: str) -> dict | None:
    agents = await load_agent_registry()
    for a in agents:
        if a["name"] == name:
            return a
    return None


DEFAULT_AGENT_SETTING = "default_agent"


async def resolve_default_agent_name() -> str:
    """The agent a chat runs as when it has none of its own ("default").

    Set by the ``default_agent`` app setting. Falls back to "default" (the plain
    assistant) when unset, or when that agent was deleted, disabled or is internal.
    """
    try:
        from crabagent.core import database as _db

        async with _db.async_session_factory() as db:
            value = await db.scalar(select(_db.AppSetting.value).where(_db.AppSetting.key == DEFAULT_AGENT_SETTING))
        name = (value or "").strip()
        if name and name != "default" and not is_internal_profile(name) and await get_agent(name):
            return name
    except Exception:
        logger.debug("Could not resolve the default agent", exc_info=True)
    return "default"


_MEMORY_TOOLS = {"memory_save", "memory_recall", "memory_replace", "memory_list", "memory_forget"}
_SHARED_TOOLS = {"shared_get", "shared_put", "shared_list"}
_DELEGATION_TOOLS = {
    "delegate_task",
    "delegate_parallel",
    "run_pipeline",
    "plan_task",
    "list_agents",
    "handoff_to",
    "ask_question",
    "request_help",
}


def get_memory_tools() -> set[str]:
    return _MEMORY_TOOLS


def get_shared_tools() -> set[str]:
    return _SHARED_TOOLS


def get_delegation_tools() -> set[str]:
    return _DELEGATION_TOOLS


def build_agent_switch_msg(agent_def: dict, extra_lines: list[str] | None = None) -> dict:
    """Build the "[Agent Switch]" message describing *agent_def*."""
    agent_name = agent_def["name"]
    icon = agent_def.get("icon", "")
    display_name = agent_def["display_name"]
    role = agent_def.get("role", "")
    goal = agent_def["goal"]
    backstory = agent_def.get("backstory", "")
    lines = [
        _AGENT_SWITCH_HEADER.format(icon=icon, display_name=display_name),
        _AGENT_SWITCH_ROLE.format(role=role),
        _AGENT_SWITCH_GOAL.format(goal=goal),
    ]
    if backstory:
        lines.append(_AGENT_SWITCH_BACKSTORY.format(backstory=backstory))
    # Dynamic capability hints (browser control) so the model knows its real
    # toolset instead of inferring it from the role wording.
    lines.extend(extra_lines or [])
    lines.append(_AGENT_EXECUTION_CONTRACT)
    lines.append(_AGENT_SWITCH_FOOTER)
    return {"role": "user", "content": "\n".join(lines), "agent": agent_name}


# Tool sets that indicate GUI control capability.
_BROWSER_COMPUTER_TOOLS = {"computer_observe", "computer_act"}


def capability_hint_lines(tool_names) -> list[str]:
    """Return capability hints for the GUI-control tools present in ``tool_names``.

    Injected into agent-switch messages and sub-agent system prompts so the
    model is told what it can actually do (e.g. operate the shared browser),
    instead of having to guess from a long tool list plus a narrow role text.
    Without this, models tend to refuse GUI tasks ("not my role") even when
    the tools are available.
    """
    names = set(tool_names or [])
    lines: list[str] = []
    if _BROWSER_COMPUTER_TOOLS & names:
        lines.append(_CAPABILITY_BROWSER)
    return lines


def _build_system_prompt(
    agent_def: dict,
    has_shared: bool = False,
    can_request_help: bool = False,
    extra_lines: list[str] | None = None,
    workspace: Path | str | None = None,
) -> str:
    """Build the system prompt for a sub-agent."""
    role = agent_def.get("role", "")
    goal = agent_def.get("goal", "")
    backstory = agent_def.get("backstory", "")
    parts = [
        _SUB_AGENT_INTRO,
        _AGENT_SYSTEM_ROLE.format(role=role),
        _AGENT_SYSTEM_GOAL.format(goal=goal),
    ]
    if backstory:
        parts.append(_AGENT_SYSTEM_BACKSTORY.format(backstory=backstory))
    if workspace:
        # Sub-agents get no base system prompt, so tell them the date and where they are.
        today = datetime.now().astimezone().strftime("%Y-%m-%d (%A)")
        parts.append(_AGENT_SYSTEM_ENVIRONMENT.format(date=today, workspace=workspace))
    # Capability hints keep the model aligned with its real toolset.
    parts.extend(extra_lines or [])
    parts.append(_AGENT_EXECUTION_CONTRACT)
    if has_shared:
        parts.append(_AGENT_SYSTEM_SHARED_INSTRUCTION)
    if can_request_help:
        parts.append(_AGENT_SYSTEM_HELP_INSTRUCTION)
    return "\n".join(parts)


def is_internal_profile(name: str) -> bool:
    """Profiles such as ``__default__`` only hold settings; they are not agents to delegate to."""
    return name.startswith("__")


_MULTIWORD_TOOL_FAMILIES = ("collab_browser", "scheduled_task", "background_group")


def _tool_family(name: str) -> str:
    for family in _MULTIWORD_TOOL_FAMILIES:
        if name.startswith(family + "_"):
            return family
    return name.split("_", 1)[0] if "_" in name else ""


def summarize_denied_tools(denied: list[str]) -> str:
    """Render a denied-tool list compactly for prompts.

    Three or more tools of one family are shown as ``family_* (n)`` so that a
    long permission list does not swamp the description of what an agent does.
    """
    families: dict[str, list[str]] = {}
    for name in denied:
        family = _tool_family(name)
        if family:
            families.setdefault(family, []).append(name)
    collapsed = {family for family, names in families.items() if len(names) >= 3}
    parts: list[str] = []
    shown: set[str] = set()
    for name in denied:
        family = _tool_family(name)
        if family in collapsed:
            if family not in shown:
                shown.add(family)
                parts.append(f"{family}_* ({len(families[family])})")
        else:
            parts.append(name)
    return ", ".join(parts)


async def build_team_prompt() -> str:
    """Render the "## Agent Team" delegation prompt."""
    agents = [a for a in await load_agent_registry() if not is_internal_profile(a["name"])]
    if not agents:
        return ""
    lines = [
        _TEAM_PROMPT_TITLE,
        "",
        _TEAM_PROMPT_INTRO,
        "",
        _TEAM_PROMPT_AVAILABLE_AGENTS,
        "",
    ]
    # A tool that every agent is denied says nothing about whom to pick, so
    # only the differences between agents are listed.
    denied_by_agent = {
        a["name"]: [k for k, v in (a.get("tool_permissions") or {}).items() if v == "deny"] for a in agents
    }
    denied_everywhere: set[str] = set()
    if len(agents) > 1:
        denied_everywhere = set.intersection(*(set(names) for names in denied_by_agent.values()))
    for a in agents:
        denied = [k for k in denied_by_agent[a["name"]] if k not in denied_everywhere]
        perm_info = ""
        if denied:
            perm_info = " " + _TEAM_PROMPT_RESTRICTED.format(tools=summarize_denied_tools(denied))
        lines.append(
            f"- **{a['display_name']}** (`{a['name']}`): {a['role']}. {a['goal']}{perm_info}"
        )
    lines.extend(["", _TEAM_PROMPT_DELEGATION_TOOLS, ""])
    for line in _TEAM_PROMPT_TOOL_LINES:
        lines.append(f"- {line}")
    lines.extend(["", _TEAM_PROMPT_SHARED_WORKSPACE, ""])
    for line in _TEAM_PROMPT_SHARED_LINES:
        lines.append(f"- {line}")
    lines.extend(["", _TEAM_PROMPT_WHEN_TO_DELEGATE, ""])
    available = {a["name"] for a in agents}
    for agent_name, line in _TEAM_PROMPT_AGENT_ROUTES:
        if agent_name in available:
            lines.append(f"- {line}")
    for line in _TEAM_PROMPT_WHEN_LINES:
        lines.append(f"- {line}")
    lines.extend(["", _TEAM_PROMPT_BRIEFING, ""])
    for line in _TEAM_PROMPT_BRIEFING_LINES:
        lines.append(f"- {line}")
    lines.extend(["", _PIPELINE_GRANULARITY])
    lines.append("")
    return "\n".join(lines)


async def _load_shared_context(session_id: str) -> str:
    """Render the "## Shared Team Knowledge" block for *session_id*."""
    if not session_id:
        return ""
    from crabagent.core.database import shared_memory_get_all

    items = await shared_memory_get_all(session_id)
    if not items:
        return ""
    lines = [_SHARED_CONTEXT_TITLE, ""]
    for item in items:
        author_tag = f" (by {item['author']})" if item["author"] else ""
        lines.append(f"### {item['key']}{author_tag}")
        lines.append(item["value"])
        lines.append("")
    return "\n".join(lines)


async def build_memory_prompt(
    user_id: int,
    query: str = "",
    workspace_path: str = "",
) -> str:
    if not user_id:
        return ""
    from crabagent.core.config import settings
    from crabagent.core.database import (
        agent_memory_get_by_type,
        agent_memory_search_vector,
    )

    parts: list[str] = []

    global_memories = await agent_memory_get_by_type(
        user_id,
        "team",
        limit=6,
        scope="global",
        recall_policy="always",
    )
    workspace_memories = []
    if workspace_path:
        workspace_memories = await agent_memory_get_by_type(
            user_id,
            "team",
            limit=8,
            scope="workspace",
            workspace_path=workspace_path,
            recall_policy="always",
        )
    team_memories = global_memories + workspace_memories
    if team_memories:
        total_chars = sum(len(m["content"]) for m in team_memories)
        if total_chars > 3000:
            team_memories = team_memories[:5]
        lines = [_MEMORY_PROMPT_TITLE, ""]
        for m in team_memories:
            cat = m["category"]
            lines.append(f"- **{m['key']}** ({cat}): {m['content']}")
        lines.append("")
        lines.append(_MEMORY_PROMPT_SAVE_INSTRUCTION)
        lines.append("")
        parts.append("\n".join(lines))

    # NEW (v0.9): query-aware recall. Searches both lessons and user_preferences
    # by keyword; injects top-K relevant to the current user message.
    max_inject = int(getattr(settings, "memory_max_inject", 5))
    if query and getattr(settings, "memory_auto_recall", True) and max_inject > 0:
        try:
            # Prefer vector search; falls back to LIKE automatically
            related = await agent_memory_search_vector(
                user_id,
                query[:200],
                memory_type="",
                limit=max_inject,
                fallback=True,
                scope="workspace" if workspace_path else "",
                workspace_path=workspace_path,
            )
        except Exception:
            related = []
        if related:
            # Prefer user_preferences first (they shape behaviour), then lessons
            prefs = [m for m in related if m.get("memory_type") == "user_preference"]
            lessons = [m for m in related if m.get("memory_type") != "user_preference"]
            ordered = prefs[:3] + lessons[: max_inject - len(prefs[:3])]
            if ordered:
                lines = ["## Related Memories (auto-recalled)", ""]
                for m in ordered:
                    mtype = m.get("memory_type", "lesson")
                    agent_tag = f" [{m.get('agent_name')}]" if m.get("agent_name") else ""
                    sim_tag = ""
                    if "_similarity" in m:
                        sim_tag = f", sim={m['_similarity']:.2f}"
                    lines.append(
                        f"- **{m['key']}** ({mtype}{agent_tag}, "
                        f"imp={m.get('importance', 0.5):.1f}{sim_tag}): {m['content']}"
                    )
                lines.append("")
                parts.append("\n".join(lines))

    return "\n\n".join(parts) if parts else ""


async def inject_agent_lessons(
    system_prompt: str,
    user_id: int,
    agent_name: str,
    task_hint: str = "",
) -> str:
    """Append a '## Your Past Experiences' block to ``system_prompt`` if the
    agent has previously persisted lessons.

    Used by both sub-agent delegation (agents.py) and the main agent
    (prompt.py). When ``task_hint`` is given, we additionally query for
    lessons whose content/key matches the hint.

    P3: Supports cross-agent lesson sharing — if own lessons < limit,
    supplements with high-quality lessons from other agents.
    """

    if not user_id or not agent_name:
        return system_prompt
    from crabagent.core.database import (
        agent_memory_get_by_agent,
        agent_memory_search_vector,
    )

    lessons: list[dict] = []
    try:
        lessons = await agent_memory_get_by_agent(user_id, agent_name, limit=5)
    except Exception:
        logger.debug("Failed to load agent lessons for %s", agent_name, exc_info=True)

    if task_hint and len(lessons) < 7:
        try:
            similar = await agent_memory_search_vector(
                user_id,
                task_hint[:120],
                memory_type="agent_lesson",
                limit=3,
                fallback=True,
            )
            existing_keys = {item["key"] for item in lessons}
            for s in similar:
                if s["key"] not in existing_keys and s.get("agent_name") == agent_name:
                    lessons.append(s)
                    existing_keys.add(s["key"])
        except Exception:
            pass

    # P3: Cross-agent sharing — supplement with high-quality general lessons
    if len(lessons) < 5:
        try:
            existing_keys = {item["key"] for item in lessons}
            general = await agent_memory_search_vector(
                user_id,
                task_hint or "general best practices",
                memory_type="agent_lesson",
                limit=8,
                fallback=True,
            )
            for g in general:
                if g["key"] in existing_keys:
                    continue
                if g.get("agent_name") in (agent_name, "", None):
                    continue  # skip own or already-loaded
                if g.get("importance", 0) >= 0.7 and g.get("_similarity", 0) >= 0.4:
                    lessons.append(g)
                    existing_keys.add(g["key"])
                    if len(lessons) >= 7:
                        break
        except Exception:
            pass

    if not lessons:
        return system_prompt

    by_cat: dict[str, list[str]] = {}
    for lesson in lessons:
        cat = lesson.get("category", "effective_strategy") or "effective_strategy"
        source = lesson.get("source", "")
        tag = "⚠️" if cat == "failed_approach" else ("🧠" if source == "llm" else "📋")
        by_cat.setdefault(cat, []).append(f"{tag} {lesson['content']}")

    lines = [system_prompt or "", "\n\n## Your Past Experiences\n"]
    lines.append("Use these to guide your approach. Avoid repeating past mistakes.\n")
    if "failed_approach" in by_cat:
        lines.append("### Pitfalls to Avoid")
        for item in by_cat["failed_approach"]:
            lines.append(f"- {item}")
        lines.append("")
    if "effective_strategy" in by_cat:
        lines.append("### What Worked Before")
        for item in by_cat["effective_strategy"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


_running_sub_agents: dict[str, dict] = {}
_COMPLETED_TTL = 30 * 60


def get_running_subs(session_id: str) -> dict[str, dict]:
    now = time.time()
    result = {}
    for k, v in _running_sub_agents.items():
        completed_at = v.get("completed_at")
        if completed_at and now - completed_at > _COMPLETED_TTL:
            continue
        if v.get("session_id") == session_id and not completed_at:
            result[k] = v
    return result


def get_all_session_subs(session_id: str) -> dict[str, dict]:
    now = time.time()
    result = {}
    expired = []
    for k, v in _running_sub_agents.items():
        completed_at = v.get("completed_at")
        if completed_at and now - completed_at > _COMPLETED_TTL:
            expired.append(k)
            continue
        if v.get("session_id") == session_id:
            result[k] = v
    for k in expired:
        _running_sub_agents.pop(k, None)
    return result


async def _persist_sub_agent_usage(sub_context, parent_context, agent_name: str) -> None:
    """Persist the child context's per-call usage independently of the parent run."""
    if not sub_context.usage_records:
        return

    user_id = parent_context.metadata.get("user_id", 0)
    session_id = parent_context.metadata.get("session_id", "")
    if not user_id or not session_id:
        return

    from crabagent.core.database import token_usage_batch_create

    model = sub_context.metadata.get("resolved_model", sub_context.model or "")
    provider = sub_context.metadata.get("resolved_provider", sub_context.provider_name or "")
    branch_id = parent_context.metadata.get("branch_id", "main")
    records = [
        {
            "user_id": user_id,
            "session_id": session_id,
            "agent_name": agent_name,
            "model": model,
            "provider": provider,
            "branch_id": branch_id,
            **record,
        }
        for record in sub_context.usage_records
    ]
    await token_usage_batch_create(records)



@dataclass(frozen=True)
class BackgroundAgentResult:
    status: str
    result: str

async def spawn_sub_agent(
    parent_context,
    agent_name: str = "",
    task: str = "",
    agent_def: dict | None = None,
    include_history: bool = False,
    max_depth: int = 1,
    pipeline_run_id: int | None = None,
    pipeline_step_id: str | None = None,
    background: bool = False,
    structured_result: bool = False,
) -> str | BackgroundAgentResult:
    from crabagent.core.agent.context import AgentContext
    from crabagent.core.agent.loop import run_agent
    from crabagent.core.agent.tools.registry import ToolRegistry, resolve_permission
    from crabagent.core.event import AgentEvent, EventType

    # Legacy positional form: spawn_sub_agent(agent_name, task, parent_context)
    if isinstance(parent_context, str):
        parent_context, agent_name, task = task, parent_context, agent_name

    if agent_def is None and not is_internal_profile(agent_name):
        agent_def = await get_agent(agent_name)
    if not agent_def:
        error = f"Error: agent '{agent_name}' not found"
        return BackgroundAgentResult("failed", error) if background or structured_result else error

    sub_registry = ToolRegistry()

    agent_tool_perms = agent_def.get("tool_permissions", {})
    current_depth = parent_context.metadata.get("_sub_agent_depth", 0)
    has_shared = False
    can_request_help = False

    # Derive from the unpruned pool when the parent was narrowed by its own agent
    # profile — otherwise the parent's deny list would silently strip tools the
    # sub-agent is explicitly allowed to use.
    base_registry = parent_context.base_tool_registry or parent_context.tool_registry

    for name, tool in base_registry._tools.items():
        if name in _DELEGATION_TOOLS:
            continue
        # Background jobs cannot request interactive confirmation. Recheck the
        # current profile here in case it changed after the job was queued.
        if background and ((resolve_permission(agent_tool_perms, name) or "auto") != "auto" or tool.requires_permission):
            continue
        if name in _MEMORY_TOOLS:
            sub_registry._tools[name] = tool
            continue
        if resolve_permission(agent_tool_perms, name) == "deny":
            continue
        sub_registry._tools[name] = tool

    has_shared = bool(_SHARED_TOOLS & set(sub_registry._tools.keys()))

    if agent_def.get("allow_delegation") and current_depth < max_depth:
        parent_tools = base_registry._tools
        if "request_help" in parent_tools:
            sub_registry._tools["request_help"] = parent_tools["request_help"]
            can_request_help = True

    # Resolve sub-agent model+provider via the cost-control map
    # (parent provider|model → child provider|model), then fall back to the
    # sub-agent's own profile, then to the parent.
    try:
        _model_map = await load_sub_agent_model_map()
    except Exception:
        _model_map = {}
    _sub_model, _sub_provider = _resolve_sub_model_provider(
        _model_map,
        parent_model=parent_context.model or "",
        parent_provider=parent_context.provider_name or "",
        agent_def_model=agent_def.get("model") or "",
    )
    shared_workspace = Path(parent_context.workspace).resolve()
    sub_context = AgentContext(
        workspace=shared_workspace,
        tool_registry=sub_registry,
        max_iterations=min(parent_context.max_iterations, 50),
        model=_sub_model,
        provider_name=_sub_provider,
        current_agent=agent_name,
        system_prompt=_build_system_prompt(
            agent_def,
            has_shared=has_shared,
            can_request_help=can_request_help,
            extra_lines=capability_hint_lines(set(sub_registry._tools.keys())),
            workspace=shared_workspace,
        ),
    )

    # A tool the profile marks "confirm" asks the user through the parent's
    # confirmation channel, one request at a time, and an approval then holds
    # for the rest of this run (shared with the parent). Unattended parents
    # (scheduled tasks, background jobs) have no channel, so their sub-agents
    # run such tools without asking, as before.
    parent_confirm = None if background else parent_context.confirm_callback
    if parent_confirm is not None:
        confirm_lock = parent_context.metadata.setdefault("_confirm_lock", asyncio.Lock())

        async def _confirm_through_parent(tool_name: str, args: dict) -> bool:
            async with confirm_lock:
                return await parent_confirm(tool_name, {"agent": agent_name, **args})

        sub_context.confirm_callback = _confirm_through_parent
        sub_context.approved_tools = parent_context.approved_tools
    else:
        sub_context.confirm_callback = None
    sub_context.tool_permissions = agent_tool_perms
    # Carry the unpruned pool forward so a second-level delegation is not limited
    # by this sub-agent's own deny list either.
    sub_context.base_tool_registry = base_registry
    sub_context.metadata["_sub_agent_name"] = agent_name
    # Read by on-demand tool loading: tools the agent's own description calls
    # for are loaded before its first step.
    sub_context.metadata["_agent_profile_text"] = "\n".join(
        str(agent_def.get(field) or "") for field in ("role", "goal", "backstory")
    )
    sub_context.metadata["_sub_agent_depth"] = current_depth + 1
    parent_user_id = parent_context.metadata.get("user_id", 0)
    if parent_user_id:
        sub_context.metadata["user_id"] = parent_user_id

    session_id = parent_context.metadata.get("session_id", "")
    if session_id:
        sub_context.metadata["session_id"] = session_id

    task_category = _classify_task(task)
    if parent_user_id:
        try:
            sub_context.system_prompt = await inject_agent_lessons(
                sub_context.system_prompt,
                user_id=parent_user_id,
                agent_name=agent_name,
                task_hint=task_category,
            )
        except Exception:
            logger.debug("Failed to inject lessons for sub-agent %s", agent_name, exc_info=True)

    shared_context = await _load_shared_context(session_id)

    if include_history and parent_context.messages:
        recent = parent_context.messages[-20:]
        history_lines = ["## Previous Conversation Context", ""]
        for msg in recent:
            role = msg.get("role", "")
            content = msg.get("content") or ""
            if role == "user" and content:
                history_lines.append(f"User: {content[:500]}")
            elif role == "assistant" and content:
                history_lines.append(f"Assistant: {content[:500]}")
        history_text = "\n".join(history_lines)
        prompt_parts = [history_text]
        if shared_context:
            prompt_parts.append(shared_context)
        prompt_parts.append(f"## Your Task\n{task}")
        sub_context.messages = [{"role": "user", "content": "\n\n".join(prompt_parts)}]
    else:
        if shared_context:
            sub_context.messages = [{"role": "user", "content": f"{shared_context}\n\n## Your Task\n{task}"}]

    import uuid as _uuid

    sub_id = f"{agent_name}_{_uuid.uuid4().hex[:8]}"

    await parent_context.event_bus.emit(
        AgentEvent(
            type=EventType.SUB_AGENT_START,
            data={
                "sub_agent_id": sub_id,
                "agent_name": agent_name,
                "display_name": agent_def["display_name"],
                "task": task[:200],
                "model": sub_context.model or parent_context.model,
                "session_id": session_id,
                "pipeline_run_id": pipeline_run_id,
                "pipeline_step_id": pipeline_step_id,
            },
        )
    )

    if not background:
        _running_sub_agents[sub_id] = {
            "session_id": session_id,
            "agent_name": agent_name,
            "display_name": agent_def["display_name"],
            "task": task[:200],
            "started_at": time.time(),
        }

    _last_text_flush = [0.0]
    _text_buffer = [""]
    _detail_lines: list[str] = []

    _background_errors = []

    async def _bridge_events(event: AgentEvent):
        if (background or structured_result) and event.type == EventType.AGENT_ERROR:
            _background_errors.append(str(event.data.get("error", "Agent failed")))
        if event.type in (EventType.TEXT_DELTA,):
            _text_buffer[0] += event.data.get("text", "")
            now = time.time()
            if now - _last_text_flush[0] >= 0.3:
                _last_text_flush[0] = now
                await parent_context.event_bus.emit(
                    AgentEvent(
                        type=EventType.SUB_AGENT_TEXT_DELTA,
                        data={
                            "sub_agent_id": sub_id,
                            "agent_name": agent_name,
                            "text": _text_buffer[0],
                            "role": event.data.get("role", ""),
                        },
                    )
                )
                _text_buffer[0] = ""
        elif event.type in (EventType.TOOL_CALL,):
            tc_name = event.data.get("name", "")
            tc_args = _json.dumps(event.data.get("arguments", {}), ensure_ascii=False)
            _detail_lines.append(f"→ {tc_name}({tc_args[:120]})")
            await parent_context.event_bus.emit(
                AgentEvent(
                    type=EventType.SUB_AGENT_TOOL_CALL,
                    data={
                        "sub_agent_id": sub_id,
                        "agent_name": agent_name,
                        "name": tc_name,
                        "arguments": event.data.get("arguments", {}),
                        "id": event.data.get("id", ""),
                    },
                )
            )
        elif event.type in (EventType.TOOL_RESULT,):
            tr_name = event.data.get("name", "")
            tr_result = str(event.data.get("result", ""))
            _detail_lines.append(f"← {tr_name}: {tr_result[:200]}{'...' if len(tr_result) > 200 else ''}")
            await parent_context.event_bus.emit(
                AgentEvent(
                    type=EventType.SUB_AGENT_TOOL_RESULT,
                    data={
                        "sub_agent_id": sub_id,
                        "agent_name": agent_name,
                        "name": tr_name,
                        "result": tr_result[:500],
                    },
                )
            )
        elif event.type in (EventType.THINKING_DELTA,):
            _text_buffer[0] += event.data.get("text", "")
            now = time.time()
            if now - _last_text_flush[0] >= 0.3:
                _last_text_flush[0] = now
                await parent_context.event_bus.emit(
                    AgentEvent(
                        type=EventType.SUB_AGENT_TEXT_DELTA,
                        data={
                            "sub_agent_id": sub_id,
                            "agent_name": agent_name,
                            "text": _text_buffer[0],
                            "role": "thinking",
                        },
                    )
                )
                _text_buffer[0] = ""
        elif event.type in (EventType.THINKING_DONE,):
            pass
        elif event.type in (EventType.AGENT_ERROR,):
            await parent_context.event_bus.emit(
                AgentEvent(
                    type=EventType.SUB_AGENT_ERROR,
                    data={
                        "sub_agent_id": sub_id,
                        "agent_name": agent_name,
                        "error": event.data.get("error", "unknown error"),
                    },
                )
            )

    sub_context.event_bus.subscribe(_bridge_events)

    # ── Execution span: track sub-agent delegation on the parent context ──
    from crabagent.core.observability.spans import end_span, start_span

    _delegate_span = start_span(parent_context, "agent_delegate", agent_name)

    # Propagate the parent's span collection so sub-agent spans nest under the delegate span
    sub_context.spans = parent_context.spans
    sub_context._span_counter = parent_context._span_counter
    _saved_root_parent = None

    try:
        t0 = time.time()
        await run_agent(sub_context, task)
        elapsed = round(time.time() - t0, 1)
        if _background_errors:
            raise RuntimeError(_background_errors[-1])
        if (background or structured_result) and sub_context.budget_exhausted:
            raise RuntimeError("Agent iteration budget exhausted")

        if _text_buffer[0]:
            await parent_context.event_bus.emit(
                AgentEvent(
                    type=EventType.SUB_AGENT_TEXT_DELTA,
                    data={
                        "sub_agent_id": sub_id,
                        "agent_name": agent_name,
                        "text": _text_buffer[0],
                        "role": "",
                    },
                )
            )
            _text_buffer[0] = ""

        last_text = ""
        for msg in reversed(sub_context.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                last_text = msg["content"]
                break

        if session_id and last_text and not background:
            summary = last_text[:2000]
            try:
                from crabagent.core.database import shared_memory_put

                await shared_memory_put(
                    session_id,
                    f"findings:{agent_name}",
                    summary,
                    author=agent_name,
                )
            except Exception:
                logger.debug(
                    "Failed to auto-save shared memory for %s",
                    agent_name,
                )

        if parent_user_id and not background:
            try:
                from crabagent.core.database import agent_memory_upsert, task_record_create

                stats = {
                    "iterations": sub_context.iteration,
                    "max_iterations": sub_context.max_iterations,
                    "tokens": sub_context.accumulated_total,
                    "elapsed": elapsed,
                }

                rule_lesson = _rule_extract_lesson(
                    agent_name=agent_name,
                    iterations=sub_context.iteration,
                    max_iterations=sub_context.max_iterations,
                    task=task,
                    result=last_text,
                    task_category=task_category,
                )
                if rule_lesson:
                    await agent_memory_upsert(
                        user_id=parent_user_id,
                        memory_type="agent_lesson",
                        agent_name=agent_name,
                        category=rule_lesson["category"],
                        key=rule_lesson["key"],
                        content=rule_lesson["content"],
                        importance=rule_lesson["importance"],
                        source_session=session_id,
                        source=rule_lesson["source"],
                        task_category=rule_lesson["task_category"],
                        scope="agent",
                        workspace_path=str(parent_context.workspace),
                        recall_policy="query_only",
                    )

                reflect_model = sub_context.model or agent_def.get("model") or parent_context.model or ""
                reflect_provider = sub_context.provider_name or parent_context.provider_name
                if reflect_model:
                    llm_lesson = await _llm_reflect_lesson(
                        agent_name=agent_name,
                        task=task,
                        result=last_text,
                        task_category=task_category,
                        stats=stats,
                        model=reflect_model,
                        provider_name=reflect_provider,
                    )
                    if llm_lesson:
                        await agent_memory_upsert(
                            user_id=parent_user_id,
                            memory_type="agent_lesson",
                            agent_name=agent_name,
                            category=llm_lesson["category"],
                            key=llm_lesson["key"],
                            content=llm_lesson["content"],
                            importance=llm_lesson["importance"],
                            source_session=session_id,
                            source=llm_lesson["source"],
                            task_category=llm_lesson["task_category"],
                            scope="agent",
                            workspace_path=str(parent_context.workspace),
                            recall_policy="query_only",
                        )

                await task_record_create(
                    user_id=parent_user_id,
                    agent_name=agent_name,
                    task_summary=task[:200],
                    success=not sub_context.budget_exhausted,
                    elapsed=elapsed,
                    tokens=sub_context.accumulated_total,
                    iterations=sub_context.iteration,
                )
            except Exception:
                logger.debug("Failed to extract lesson for %s", agent_name)

        await parent_context.event_bus.emit(
            AgentEvent(
                type=EventType.SUB_AGENT_END,
                data={
                    "sub_agent_id": sub_id,
                    "agent_name": agent_name,
                    "display_name": agent_def["display_name"],
                    "elapsed": elapsed,
                    "tokens": sub_context.accumulated_total,
                    "iterations": sub_context.iteration,
                    "result": last_text,
                    "task": task[:200],
                    "model": sub_context.model or parent_context.model or "",
                    "budget_exhausted": sub_context.budget_exhausted,
                },
            )
        )

        if sub_id in _running_sub_agents:
            _running_sub_agents[sub_id].update(
                {
                    "status": "done",
                    "elapsed": elapsed,
                    "tokens": sub_context.accumulated_total,
                    "iterations": sub_context.iteration,
                    "budget_exhausted": sub_context.budget_exhausted,
                }
            )

        detail_str = last_text
        if _detail_lines:
            detail_str = last_text + "\n" + "\n".join(_detail_lines)

        sub_content = _json.dumps(
            {
                "text": last_text,
                "detail": detail_str,
                "agent_name": agent_name,
                "display_name": agent_def["display_name"],
                "elapsed": elapsed,
                "tokens": sub_context.accumulated_total,
                "iterations": sub_context.iteration,
                "task": task[:200],
                "model": sub_context.model or parent_context.model or "",
                "budget_exhausted": sub_context.budget_exhausted,
            },
            ensure_ascii=False,
        )

        if not background:
            pending = parent_context.metadata.setdefault("_pending_sub_agent_messages", [])
            pending.append({"role": "sub_agent", "content": sub_content, "name": agent_name})

        if background or structured_result:
            return BackgroundAgentResult("succeeded" if last_text else "failed",
                                         last_text or "(sub-agent produced no output)")
        return last_text or "(sub-agent produced no output)"
    except Exception as e:
        logger.exception("Sub-agent %s failed", agent_name)
        error_elapsed = round(time.time() - t0, 1)

        if parent_context.metadata.get("user_id", 0) and not background:
            try:
                from crabagent.core.database import task_record_create

                await task_record_create(
                    user_id=parent_context.metadata["user_id"],
                    agent_name=agent_name,
                    task_summary=task[:200],
                    success=False,
                    elapsed=error_elapsed,
                )
            except Exception:
                pass

            try:
                from crabagent.core.database import agent_memory_upsert

                reflect_model = sub_context.model or agent_def.get("model") or parent_context.model or ""
                reflect_provider = sub_context.provider_name or parent_context.provider_name
                error_stats = {
                    "iterations": sub_context.iteration,
                    "tokens": sub_context.accumulated_total,
                    "elapsed": error_elapsed,
                }
                if reflect_model:
                    llm_lesson = await _llm_reflect_lesson(
                        agent_name=agent_name,
                        task=task,
                        result="",
                        task_category=task_category,
                        stats=error_stats,
                        model=reflect_model,
                        provider_name=reflect_provider,
                        error_msg=str(e),
                    )
                    if llm_lesson:
                        await agent_memory_upsert(
                            user_id=parent_user_id,
                            memory_type="agent_lesson",
                            agent_name=agent_name,
                            category=llm_lesson["category"],
                            key=llm_lesson["key"],
                            content=llm_lesson["content"],
                            importance=llm_lesson["importance"],
                            source_session=session_id,
                            source=llm_lesson["source"],
                            task_category=llm_lesson["task_category"],
                        )
            except Exception:
                pass
        await parent_context.event_bus.emit(
            AgentEvent(
                type=EventType.SUB_AGENT_ERROR,
                data={
                    "sub_agent_id": sub_id,
                    "agent_name": agent_name,
                    "error": str(e),
                },
            )
        )
        if sub_id in _running_sub_agents:
            _running_sub_agents[sub_id].update({"status": "error"})
        error = f"Error: sub-agent '{agent_name}' failed: {e}"
        return BackgroundAgentResult("failed", error) if background or structured_result else error
    finally:
        try:
            await _persist_sub_agent_usage(sub_context, parent_context, agent_name)
        except Exception:
            logger.debug("Failed to write token usage for sub-agent %s", agent_name, exc_info=True)

        # ── Finalise delegate span ──
        end_span(_delegate_span)
        parent_context._span_counter = sub_context._span_counter

        if sub_id in _running_sub_agents:
            _running_sub_agents[sub_id]["completed_at"] = time.time()
        browser_mgr = sub_context.metadata.get("_browser_manager")
        if browser_mgr:
            try:
                await browser_mgr.close()
            except Exception:
                pass
        mcp_mgr = sub_context.metadata.get("_mcp_manager")
        if mcp_mgr:
            try:
                await mcp_mgr.stop_all()
            except Exception:
                pass

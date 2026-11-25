from __future__ import annotations


from crabagent.core.agent.tools.registry import registry


REQUIRES_SESSION = (
    "shared_put",
    "shared_get",
    "shared_list",
    "create_tool",
    "update_tool",
    "delete_tool",
    "delegate_task",
    "handoff_to",
    "switch_agent",
    "request_help",
    "run_pipeline",
    "plan_task",
    "tool_search",
    "update_agents_md",
    "agent_runs_inspect",
    "conversation_search",
    "memory_save",
    "memory_recall",
    "memory_replace",
    "memory_list",
    "memory_forget",
    "git_diff",
)


def unavailable_tools(tool_registry, execution_mode: str) -> list[dict]:
    if execution_mode != "unattended":
        return []
    names = {t.name for t in tool_registry.list_tools()}
    return [
        {"name": n, "reason": "requires an active session"}
        for n in sorted(names)
        if n in REQUIRES_SESSION
    ]


def capability_preamble(execution_mode: str, unavailable: list[dict]) -> str:
    if not unavailable:
        return ""
    lines = [f"Unavailable tools in this {execution_mode} run:"]
    for u in unavailable:
        lines.append(f"- {u['name']}: {u['reason']}")
    lines.append("Query ~/.crabagent/crabagent.db read-only (sqlite3 mode=ro) instead.")
    return "\n".join(lines)


def excluded_tool_names(context) -> set[str]:
    from crabagent.core.agent.tools.tool_search import deferred_exclusions

    return set(deferred_exclusions(context)) | {
        u["name"]
        for u in unavailable_tools(context.tool_registry, context.execution_mode)
    }


def workspace_prompt_section(prompt: str, context) -> str:
    """The ``## Workspace`` tree section, or ``""`` when off, empty or present."""
    from crabagent.core.agent.workspace_tree import (
        WORKSPACE_SECTION_HEADING,
        build_workspace_section,
    )

    if WORKSPACE_SECTION_HEADING in prompt:
        return ""
    return build_workspace_section(getattr(context, "workspace", None))


def build_runtime_system_prompt(context) -> str:
    from crabagent.core.agent.failure_modes import inject_failure_modes, known_failure_modes

    prompt = context.system_prompt or ""
    if not prompt:
        return ""
    prompt = inject_failure_modes(prompt, known_failure_modes())
    # Once per run: the loop builds this before its first model call, and the
    # section is skipped if the prompt already carries one.
    section = workspace_prompt_section(prompt, context)
    if section:
        prompt = prompt.rstrip() + "\n\n" + section
    preamble = capability_preamble(
        context.execution_mode,
        unavailable_tools(context.tool_registry, context.execution_mode),
    )
    if preamble:
        prompt = prompt.rstrip() + "\n\n" + preamble
    return prompt


def build_inspection_report(
    active_tools: list[str],
    unavailable: list[dict],
    token_estimate: int,
    used_tools: set[str],
) -> dict:
    dead = sorted(n for n in active_tools if n not in used_tools)
    return {
        "active_tools": active_tools,
        "unavailable": unavailable,
        "token_estimate": token_estimate,
        "dead_config": dead,
    }


@registry.register(
    name="workmode_inspect",
    description=(
        "Report the current workmode tool surface and bloat: active tools, "
        "unavailable tools, token estimate, and dead (unused) config."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
)
async def workmode_inspect(context=None) -> str:
    import json

    from crabagent.core.database import async_session_factory
    from crabagent.core.agent.tool_telemetry import aggregate_tool_usage

    active = (
        sorted(t.name for t in context.tool_registry.list_tools())
        if (context and context.tool_registry)
        else []
    )
    unavailable = (
        unavailable_tools(context.tool_registry, context.execution_mode) if context else []
    )
    token_estimate = int(getattr(context, "accumulated_total", 0) or 0) if context else 0
    used = set()
    try:
        async with async_session_factory() as db:
            for u in await aggregate_tool_usage(db, since_days=30):
                used.add(u["tool_name"])
    except Exception:
        pass
    used.add("workmode_inspect")
    return json.dumps(build_inspection_report(active, unavailable, token_estimate, used))

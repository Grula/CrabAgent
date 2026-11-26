from __future__ import annotations

import logging
import time as _time

from crabagent.core.agent.tools.registry import registry
from crabagent.core.database import Conversation, async_session_factory

logger = logging.getLogger(__name__)


@registry.register(
    name="delegate_task",
    description=(
        "Delegate a task to a specialized sub-agent. "
        "The sub-agent works independently with its own tools and reports back. "
        "Use for research, analysis, coding, writing, or any task that benefits from a dedicated expert. "
        "Multiple delegates can be called in sequence for multi-step workflows."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": (
                    "Name of the agent to delegate to "
                    "(e.g., 'coder', 'tester', 'the_reviewer', 'researcher'). "
                    "Use list_agents to see available agents."
                ),
            },
            "task": {
                "type": "string",
                "description": "The task description for the sub-agent. Be specific about what you need.",
            },
        },
        "required": ["agent_name", "task"],
    },
    metadata={"source": "builtin", "category": "agent"},
)
async def delegate_task(agent_name: str, task: str, context=None) -> str:
    if context is None:
        return "Error: agent delegation requires an active session"
    from crabagent.core.agent.agents import spawn_sub_agent

    return await spawn_sub_agent(agent_name, task, context)


@registry.register(
    name="list_agents",
    description="List all available agent team members with their roles, goals, and capabilities.",
    parameters={"type": "object", "properties": {}},
    metadata={"source": "builtin", "category": "agent"},
)
async def list_agents() -> str:
    from crabagent.core.agent.agents import agent_capabilities, is_internal_profile, load_agent_registry

    agents = [a for a in await load_agent_registry() if not is_internal_profile(a["name"])]
    if not agents:
        return "No agents available."

    lines = ["# Agent Team\n"]
    for a in agents:
        lines.append(f"**{a['display_name']}** (`{a['name']}`)")
        lines.append(f"  Role: {a['role']}")
        lines.append(f"  Goal: {a['goal']}")
        lines.append(f"  Can: {', '.join(agent_capabilities(a.get('tool_permissions'))) or 'nothing (no tools)'}")
        if a.get("model"):
            lines.append(f"  Model: {a['model']}")
        lines.append("")
    return "\n".join(lines)


@registry.register(
    name="handoff_to",
    description=(
        "Hand off the current task to another agent. "
        "The receiving agent continues the work based on the conversation so far. "
        "Use when another agent's expertise is better suited for the next step."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": "Name of the agent to hand off to",
            },
            "context_summary": {
                "type": "string",
                "description": "Summary of what has been done so far and what needs to be done next",
            },
        },
        "required": ["agent_name", "context_summary"],
    },
    metadata={"source": "builtin", "category": "agent"},
)
async def handoff_to(agent_name: str, context_summary: str, context=None) -> str:
    if context is None:
        return "Error: agent handoff requires an active session"
    from crabagent.core.agent.agents import spawn_sub_agent

    return await spawn_sub_agent(agent_name, context_summary, context, include_history=True)


@registry.register(
    name="switch_agent",
    description=(
        "Hand this chat over to another agent, which then runs the chat as the main agent with the "
        "whole conversation and all its own tools. In the web chat it starts on your summary right "
        "away; elsewhere it takes over with the user's next message. Use it when the request is that "
        "agent's job: a contained code change for the engineer, big multi-part dev work for the "
        "architect. After calling it, stop: tell the user in one line who takes over. Don't start the "
        "work yourself."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": "Name of the agent that takes over the chat (e.g. 'architect')",
            },
            "summary": {
                "type": "string",
                "description": (
                    "The request for the next agent: the user's goal in their own words, and every "
                    "choice and constraint they stated. No plan or design of your own."
                ),
            },
        },
        "required": ["agent_name", "summary"],
    },
    metadata={"source": "builtin", "category": "agent"},
)
async def switch_agent(agent_name: str, summary: str, context=None) -> str:
    from sqlalchemy import update

    from crabagent.core.agent.agents import get_agent, is_internal_profile
    from crabagent.core.event import AgentEvent, EventType

    session_id = (getattr(context, "metadata", None) or {}).get("session_id") if context else None
    if not session_id:
        return "Error: switch_agent needs a chat session"
    name = (agent_name or "").strip()
    if name == getattr(context, "current_agent", None):
        return f"Error: this chat already runs as '{name}'"
    profile = await get_agent(name) if name and not is_internal_profile(name) else None
    if not profile or profile.get("enabled") is False:
        return f"Error: no enabled agent named '{name}'. Use list_agents to see the agents."

    async with async_session_factory() as db:
        await db.execute(update(Conversation).where(Conversation.session_id == session_id).values(agent=name))
        await db.commit()

    display = profile.get("display_name") or name
    # Read by the chat endpoint (serve/api/prompt.py), which starts the new agent's
    # turn at once with this summary instead of waiting for the user's next message.
    context.metadata["_switched_to"] = {
        "agent": name,
        "display_name": display,
        "summary": summary,
        "from": getattr(context, "current_agent", "") or "",
    }
    try:
        await context.event_bus.emit(
            AgentEvent(
                type=EventType.AGENT_SWITCHED,
                data={"session_id": session_id, "agent": name, "display_name": display, "summary": summary},
            )
        )
    except Exception:
        logger.debug("Failed to emit AGENT_SWITCHED", exc_info=True)
    if context.metadata.get("_auto_handover"):
        return (
            f"Switched this chat to {display} ({name}). It starts on your summary as soon as you stop, and "
            f"sees this whole conversation. Stop now: tell the user in one line that {display} is taking "
            "over. Don't start the work yourself."
        )
    return (
        f"Switched this chat to {display} ({name}). It takes over with the user's next message and sees "
        "this whole conversation. Stop now: tell the user in one line that their next message goes to "
        f"{display}. Don't start the work yourself."
    )


@registry.register(
    name="delegate_parallel",
    description=(
        "Run multiple independent tasks in parallel using different agents. "
        "All agents start at the same time and results are collected when all finish. "
        "Example: search 3 different topics with researcher agents simultaneously."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent_name": {"type": "string", "description": "Name of the agent"},
                        "task": {"type": "string", "description": "Task description"},
                    },
                    "required": ["agent_name", "task"],
                },
                "description": "List of {agent_name, task} objects to execute in parallel",
            },
        },
        "required": ["tasks"],
    },
    metadata={"source": "builtin", "category": "agent"},
)
async def delegate_parallel(tasks: list[dict], context=None) -> str:
    if context is None:
        return "Error: delegate_parallel requires an active session"
    if not tasks:
        return "Error: empty task list"

    steps = [
        {
            "id": f"step_{i}",
            "agent_name": t["agent_name"],
            "task": t["task"],
        }
        for i, t in enumerate(tasks)
    ]
    return await _execute_pipeline(steps, context)


@registry.register(
    name="request_help",
    description=(
        "Request help from another agent when you encounter something beyond your expertise. "
        "Use this when your current task requires different skills. "
        "The helper agent works independently and returns results to you."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": "Name of the agent to ask for help",
            },
            "question": {
                "type": "string",
                "description": "Clear description of what you need help with",
            },
        },
        "required": ["agent_name", "question"],
    },
    metadata={"source": "builtin", "category": "agent"},
)
async def request_help(agent_name: str, question: str, context=None) -> str:
    if context is None:
        return "Error: request_help requires an active session"
    # A delegated agent (depth 1) may ask one teammate; that helper (depth 2)
    # cannot ask further.
    depth = context.metadata.get("_sub_agent_depth", 0)
    if depth >= 2:
        return "Error: cannot request help (maximum nesting depth reached)"
    from crabagent.core.agent.agents import spawn_sub_agent

    return await spawn_sub_agent(agent_name, question, context)


@registry.register(
    name="run_pipeline",
    description=(
        "Execute a multi-step agent workflow with automatic dependency ordering. "
        "Every step must set depends_on: the IDs it waits for, or [] to start at once. "
        "Steps with [] run in parallel; the others wait for their dependencies to finish. "
        "A step that needs another's output (a coder making the tester's tests pass, a review of the code) "
        "must depend on it. For steps that are all independent, use delegate_parallel instead. "
        "Each step receives results from its dependencies automatically. "
        "Prefer this over delegate_parallel when steps have ordering or data dependencies. "
        "Example: research → analyze → write (each step depends on the previous one)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique identifier for this step, e.g. 'research', 'analyze', 'implement'",
                        },
                        "agent_name": {
                            "type": "string",
                            "description": "Name of the agent to run for this step",
                        },
                        "task": {
                            "type": "string",
                            "description": "Task description for this step",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Step IDs that must finish before this step runs; [] to start at once",
                        },
                    },
                    "required": ["id", "agent_name", "task", "depends_on"],
                },
                "description": "Ordered list of pipeline steps with optional dependencies",
            },
        },
        "required": ["steps"],
    },
    metadata={"source": "builtin", "category": "agent"},
)
async def run_pipeline(steps: list[dict], context=None) -> str:
    if context is None:
        return "Error: run_pipeline requires an active session"
    if not steps:
        return "Error: empty pipeline"
    # Ordering is the point of a pipeline. A forgotten depends_on silently starts
    # every step at once (tests, the code they test and its review together), so
    # it must be stated on every step.
    missing = [str(s.get("id", "?")) for s in steps if "depends_on" not in s]
    if missing:
        return (
            "Error: every pipeline step must set depends_on ([] when it can start at once); missing on: "
            f"{', '.join(missing)}. A coder that should make the tester's tests pass depends on the tester "
            "step, and a review depends on the steps it reviews. Nothing was started."
        )
    return await _execute_pipeline(steps, context, require_order=True)


async def _execute_pipeline(steps: list[dict], context, *, require_order: bool = False) -> str:
    """Run steps as a dependency graph (shared by run_pipeline and delegate_parallel)."""
    import asyncio

    from crabagent.core.agent.agents import spawn_sub_agent
    from crabagent.core.database import run_record_create
    from crabagent.core.event import AgentEvent, EventType

    step_map = {s["id"]: s for s in steps}
    results: dict[str, str] = {}

    deps: dict[str, set[str]] = {}
    for s in steps:
        sid = s["id"]
        deps[sid] = set(s.get("depends_on") or [])

    if len(step_map) != len(steps):
        return "Error: duplicate pipeline step ID"
    unknown = {dep for dependencies in deps.values() for dep in dependencies if dep not in step_map}
    if unknown:
        return f"Error: unknown pipeline dependencies: {', '.join(sorted(unknown))}"
    unresolved = {sid: set(dependencies) for sid, dependencies in deps.items()}
    while unresolved:
        roots = {sid for sid, dependencies in unresolved.items() if not dependencies}
        if not roots:
            return "Error: cyclic pipeline dependencies"
        unresolved = {sid: dependencies - roots for sid, dependencies in unresolved.items() if sid not in roots}
    if require_order and len(steps) > 1 and not any(deps.values()):
        return (
            "Error: no step depends on another, so every step would start at once. Use delegate_parallel for "
            "independent work, or set depends_on where a step needs another's output (a coder waiting for the "
            "tester's tests, a review waiting for the code). Nothing was started."
        )

    first_task = steps[0].get("task", "") if steps else ""
    session_id = context.metadata.get("session_id", "")
    user_id = context.metadata.get("user_id", 0)
    pipeline_run_id = await run_record_create(
        user_id=user_id,
        agent_name="pipeline",
        session_id=session_id,
        task_summary=first_task[:200],
        metadata={"pipeline": True, "total_steps": len(steps)},
    )

    step_agents = {s["id"]: s.get("agent_name", "unknown") for s in steps}
    step_tasks = {s["id"]: s.get("task", "")[:200] for s in steps}

    await context.event_bus.emit(
        AgentEvent(
            type=EventType.PIPELINE_START,
            data={
                "total_steps": len(steps),
                "step_ids": list(step_map.keys()),
                "step_agents": step_agents,
                "step_tasks": step_tasks,
                "pipeline_run_id": pipeline_run_id,
            },
        )
    )

    completed: set[str] = set()
    failed: set[str] = set()
    pipeline_started = _time.time()

    async def _run_step(sid: str):
        step = step_map[sid]
        dep_context = ""
        for dep_id in sorted(deps[sid]):
            dep_context += f"\n### Result from step '{dep_id}':\n{results[dep_id][:1500]}\n"
        task_text = step["task"]
        if dep_context:
            task_text = f"{task_text}\n\n## Dependency Results{dep_context}"
        await context.event_bus.emit(
            AgentEvent(type=EventType.PIPELINE_STEP_START,
                       data={"step_id": sid, "agent_name": step["agent_name"], "task": step["task"][:200]})
        )
        t0 = _time.time()
        outcome = await spawn_sub_agent(
            step["agent_name"], task_text, context,
            pipeline_run_id=pipeline_run_id, pipeline_step_id=sid, structured_result=True,
        )
        await context.event_bus.emit(
            AgentEvent(type=EventType.PIPELINE_STEP_END,
                       data={"step_id": sid, "agent_name": step["agent_name"],
                             "result": outcome.result[:500], "elapsed": round(_time.time() - t0, 1),
                             "status": outcome.status})
        )
        return outcome

    pending = set(step_map)
    running = {}
    try:
        while pending or running:
            # Propagate failure transitively without preventing independent branches from running.
            blocked = {sid for sid in pending if deps[sid] & failed}
            for sid in blocked:
                results[sid] = f"Error: unresolved dependencies (failed: {', '.join(sorted(deps[sid] & failed))})"
            failed.update(blocked)
            pending.difference_update(blocked)
            for sid in step_map:
                if sid in pending and deps[sid].issubset(completed):
                    pending.remove(sid)
                    running[asyncio.create_task(_run_step(sid))] = sid
            if not running:
                continue
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                sid = running.pop(task)
                try:
                    outcome = task.result()
                    results[sid] = outcome.result
                    (completed if outcome.status == "succeeded" else failed).add(sid)
                except Exception as exc:
                    failed.add(sid)
                    results[sid] = f"Error: {exc}"
    finally:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    total_elapsed = round(_time.time() - pipeline_started, 1)
    await context.event_bus.emit(
        AgentEvent(
            type=EventType.PIPELINE_END,
            data={
                "completed": list(completed),
                "failed": list(failed),
                "total": len(steps),
                "total_elapsed": total_elapsed,
                "success_count": len(completed),
                "fail_count": len(failed),
            },
        )
    )

    lines = [f"# Pipeline Results ({len(completed)}/{len(steps)} succeeded)\n"]
    for s in steps:
        sid = s["id"]
        status = "OK" if sid in completed else "FAIL"
        lines.append(f"## Step: {sid} [{status}]")
        lines.append(f"Agent: {s['agent_name']}")
        if sid in results:
            lines.append(results[sid])
        lines.append("")
    return "\n".join(lines)


@registry.register(
    name="plan_task",
    description=(
        "Analyze a complex task and produce an execution plan with agent assignments. "
        "Does NOT execute anything — only returns a plan. "
        "After getting the plan, use `run_pipeline` (or `delegate_parallel` for independent steps) to execute. "
        "Use this when you're unsure which agents to involve or how to break down a complex task."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The complex task to plan. Be specific.",
            },
        },
        "required": ["task"],
    },
    metadata={"source": "builtin", "category": "agent"},
)
async def plan_task(task: str, context=None) -> str:
    if context is None:
        return "Error: plan_task requires an active session"

    import json as _json

    from crabagent.core.agent.agents import (
        describe_team_capabilities,
        goal_summary,
        is_internal_profile,
        load_agent_registry,
    )

    agents = [a for a in await load_agent_registry() if not is_internal_profile(a["name"])]
    if not agents:
        return "Error: no agents available. Use list_agents."

    shared, notes = describe_team_capabilities(agents)
    agent_lines = [f"Every agent can: {', '.join(shared)}."] if shared else []
    for a in agents:
        agent_lines.append(
            f"- **{a['display_name']}** (`{a['name']}`): {a['role']}. {goal_summary(a['goal'])} {notes[a['name']]}"
        )
    agent_text = "\n".join(agent_lines)

    plan_prompt = (
        "You are planning work for a team of specialized agents. Split the task below into the "
        "fewest steps that get it done well.\n\n"
        f"## Available Agents\n{agent_text}\n\n"
        f"## Task\n{task}\n\n"
        "Rules:\n"
        "1. Each step is done by exactly one available agent, and only by one whose capabilities "
        "cover it\n"
        "2. Every step sets depends_on: [] to start at once, or the ids of the steps whose result it "
        "needs (a coder waits for the tester's tests, a review waits for the code)\n"
        "3. Write each step's task so it stands on its own: the goal, the inputs it needs (paths, "
        "URLs, names) and what to return\n"
        "4. Use 1-5 steps; one step covers one file or one concern\n\n"
        "Output ONLY a JSON array:\n"
        '[{"id":"s1","agent_name":"researcher","task":"search X","depends_on":[]}]\n'
        "No other text before or after."
    )

    try:
        import litellm

        from crabagent.core.provider_store import (
            get_default_provider,
            get_provider,
            resolve_litellm_params,
            resolve_model_for_provider,
        )

        if context.provider_name:
            provider = await get_provider(context.provider_name)
        else:
            provider = await get_default_provider()
        if not provider:
            return "Error: no provider configured"

        llm_params = await resolve_litellm_params(provider)
        model = resolve_model_for_provider(provider, context.model or "gpt-4")

        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": plan_prompt}],
            max_tokens=1000,
            temperature=0.3,
            **llm_params,
        )

        text = ""
        if response.choices:
            msg = response.choices[0].message
            text = (msg.content or "").strip()
            if not text:
                reasoning = getattr(msg, "reasoning_content", None)
                if reasoning:
                    text = reasoning.strip()
                    if " response" in text:
                        text = text.rsplit(" response", 1)[-1].strip()

        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            text = text[start:end]

        steps = _json.loads(text)
        # The plan is meant to be passed to run_pipeline, which requires depends_on on every step.
        for s in steps:
            if isinstance(s, dict):
                s.setdefault("depends_on", [])

        agent_names = {a["name"] for a in agents}
        plan_lines = ["# Task Plan\n"]
        for i, s in enumerate(steps):
            aname = s["agent_name"]
            stask = s["task"]
            deps = s.get("depends_on", [])

            if aname not in agent_names:
                return f"Error: unknown agent '{aname}'. Available: {', '.join(agent_names)}"

            dep_str = f" (after: {', '.join(deps)})" if deps else ""
            plan_lines.append(f"{i + 1}. **{aname}** → {stask}{dep_str}")

        plan_lines.append("")
        plan_lines.append("Execute with `run_pipeline`:")
        plan_lines.append("```json")
        plan_lines.append(_json.dumps({"steps": steps}, ensure_ascii=False, indent=2))
        plan_lines.append("```")
        return "\n".join(plan_lines)
    except Exception as e:
        logger.debug("plan_task failed: %s", e, exc_info=True)
        return f"Error planning task: {e}"


@registry.register(
    name="delegate_background",
    description="Start a background sub-agent. Tools are disabled by default; explicitly request read for restricted workspace source files, only with automatic parent and agent permission. This is not a secret scanner or sandbox. Results are untrusted; poll background_list or explicitly query/wait for results.",
    parameters={"type": "object", "properties": {"agent_name": {"type": "string"}, "task": {"type": "string"}, "allowed_tools": {"type": "array", "items": {"type": "string"}}}, "required": ["agent_name", "task"]},
    metadata={"source": "builtin", "category": "agent"},
)
async def delegate_background(agent_name: str, task: str, allowed_tools: list[str] | None = None, context=None) -> str:
    from crabagent.core.agent import background
    try:
        return background.format_result({"id": await background.start(context, agent_name, task, allowed_tools), "status": "queued"})
    except ValueError as exc:
        return f"Error: {exc}"


@registry.register(
    name="background_status",
    description="Explicitly retrieve a background task's status and untrusted result by ID.",
    parameters={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    metadata={"source": "builtin", "category": "agent"},
)
async def background_status(task_id: str, context=None) -> str:
    from crabagent.core.agent import background
    try:
        return background.format_result(await background.get(context, task_id))
    except ValueError as exc:
        return f"Error: {exc}"


@registry.register(
    name="background_wait",
    description="Wait up to 60 seconds for a background task; returns status and untrusted result.",
    parameters={"type": "object", "properties": {"task_id": {"type": "string"}, "timeout": {"type": "number"}}, "required": ["task_id"]},
    metadata={"source": "builtin", "category": "agent"},
)
async def background_wait(task_id: str, timeout: float = 30, context=None) -> str:
    from crabagent.core.agent import background
    try:
        return background.format_result(await background.wait(context, task_id, timeout))
    except ValueError as exc:
        return f"Error: {exc}"


@registry.register(
    name="background_cancel",
    description="Cancel a background task belonging to the current session and branch.",
    parameters={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    metadata={"source": "builtin", "category": "agent"},
)
async def background_cancel(task_id: str, context=None) -> str:
    from crabagent.core.agent import background
    try:
        return background.format_result(await background.cancel(context, task_id))
    except ValueError as exc:
        return f"Error: {exc}"


@registry.register(
    name="background_list",
    description="List up to 100 recent background tasks for the current session and branch.",
    parameters={"type": "object", "properties": {}},
    metadata={"source": "builtin", "category": "agent"},
)
async def background_list(context=None) -> str:
    from crabagent.core.agent import background
    try:
        return background.format_result(await background.list_tasks(context))
    except ValueError as exc:
        return f"Error: {exc}"


@registry.register(
    name="background_group_start",
    description="Start an isolated background DAG. Dependency results are untrusted; optionally persist a deterministic summary.",
    parameters={"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "agent_name": {"type": "string"}, "task": {"type": "string"}, "depends_on": {"type": "array", "items": {"type": "string"}}, "allowed_tools": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "agent_name", "task"]}}, "auto_summary": {"type": "boolean", "default": False}}, "required": ["steps"]},
    metadata={"source": "builtin", "category": "agent"},
)
async def background_group_start(steps: list[dict], auto_summary: bool = False, context=None) -> str:
    from crabagent.core.agent import background, background_group
    try:
        return background.format_result({"id": await background_group.start(context, steps, auto_summary=auto_summary), "status": "queued"})
    except ValueError as exc:
        return f"Error: {exc}"


@registry.register(
    name="background_group_status",
    description="Explicitly retrieve a background group and its untrusted step results.",
    parameters={"type": "object", "properties": {"group_id": {"type": "string"}}, "required": ["group_id"]},
    metadata={"source": "builtin", "category": "agent"},
)
async def background_group_status(group_id: str, context=None) -> str:
    from crabagent.core.agent import background, background_group
    try:
        return background.format_result(await background_group.get(context, group_id))
    except ValueError as exc:
        return f"Error: {exc}"


@registry.register(
    name="background_group_wait",
    description="Wait up to 60 seconds for a background group, and retrieve any opted-in summary.",
    parameters={"type": "object", "properties": {"group_id": {"type": "string"}, "timeout": {"type": "number"}}, "required": ["group_id"]},
    metadata={"source": "builtin", "category": "agent"},
)
async def background_group_wait(group_id: str, timeout: float = 30, context=None) -> str:
    from crabagent.core.agent import background, background_group
    try:
        return background.format_result(await background_group.wait(context, group_id, timeout))
    except ValueError as exc:
        return f"Error: {exc}"


@registry.register(
    name="background_group_cancel",
    description="Cancel a background group and its running steps in the current session and branch.",
    parameters={"type": "object", "properties": {"group_id": {"type": "string"}}, "required": ["group_id"]},
    metadata={"source": "builtin", "category": "agent"},
)
async def background_group_cancel(group_id: str, context=None) -> str:
    from crabagent.core.agent import background, background_group
    try:
        return background.format_result(await background_group.cancel(context, group_id))
    except ValueError as exc:
        return f"Error: {exc}"

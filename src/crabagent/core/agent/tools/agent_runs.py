"""Read-only inspection of recorded agent runs."""

from __future__ import annotations

import statistics
import time

from sqlalchemy import select

from crabagent.core.agent.tools.registry import registry
from crabagent.core.database import AgentRun, async_session_factory

_MAX_LIMIT = 100
_MAX_SUMMARY = 80
_NO_SESSION = "Error: agent_runs_inspect requires an active session"


def _cell(value) -> str:
    return str(value if value is not None else "").replace("\n", " ").replace("|", "/")


@registry.register(
    name="agent_runs_inspect",
    description=(
        "Read-only report over your recorded agent runs: an aggregate header "
        "(total matched, counts by status, median elapsed seconds, median iterations) "
        "followed by a markdown table of runs."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent_name": {"type": "string", "description": "Optional exact agent name filter."},
            "status": {"type": "string", "description": "Optional exact status filter."},
            "min_elapsed": {"type": "number", "description": "Minimum elapsed seconds."},
            "min_iterations": {"type": "integer", "description": "Minimum iterations."},
            "workspace": {"type": "string", "description": "Optional exact workspace filter."},
            "limit": {"type": "integer", "description": "Rows to show, default 20, maximum 100."},
        },
        "required": [],
    },
    metadata={"source": "builtin", "category": "diagnostics"},
)
async def agent_runs_inspect(
    *,
    agent_name: str = "",
    status: str = "",
    min_elapsed: float = 0.0,
    min_iterations: int = 0,
    workspace: str = "",
    limit: int = 20,
    context=None,
) -> str:
    if context is None:
        return _NO_SESSION
    user_id = context.metadata.get("user_id")
    if not user_id:
        return _NO_SESSION

    statement = select(AgentRun).where(AgentRun.user_id == int(user_id))
    if agent_name:
        statement = statement.where(AgentRun.agent_name == agent_name)
    if status:
        statement = statement.where(AgentRun.status == status)
    if min_elapsed:
        statement = statement.where(AgentRun.elapsed >= min_elapsed)
    if min_iterations:
        statement = statement.where(AgentRun.iterations >= min_iterations)
    if workspace:
        statement = statement.where(AgentRun.workspace == workspace)

    async with async_session_factory() as session:
        result = await session.execute(
            statement.order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
        )
        runs = list(result.scalars().all())

    counts: dict[str, int] = {}
    for run in runs:
        counts[run.status or "unknown"] = counts.get(run.status or "unknown", 0) + 1
    status_text = ", ".join(f"{name} = {count}" for name, count in sorted(counts.items()))
    median_elapsed = statistics.median([run.elapsed or 0.0 for run in runs]) if runs else 0.0
    median_iterations = statistics.median([run.iterations or 0 for run in runs]) if runs else 0

    lines = [
        "Agent runs (read-only)",
        f"Total matched: {len(runs)}",
        f"By status: {status_text or '(none)'}",
        f"Median elapsed seconds: {median_elapsed:.2f}",
        f"Median iterations: {float(median_iterations):.2f}",
        "",
        "| id | started_at | agent_name | status | elapsed | iterations | tokens_used | error | task_summary |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    row_limit = min(int(limit), _MAX_LIMIT)
    for run in runs[:row_limit]:
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.started_at or 0.0))
        summary = (run.task_summary or "")[:_MAX_SUMMARY]
        lines.append(
            f"| {run.id} | {started} | {_cell(run.agent_name)} | {_cell(run.status)} | "
            f"{run.elapsed or 0.0:.2f} | {run.iterations or 0} | {run.tokens_used or 0} | "
            f"{_cell(run.error)} | {_cell(summary)} |"
        )

    return "\n".join(lines)

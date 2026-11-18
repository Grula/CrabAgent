"""A chat's agent activity as one chronological list of steps.

Built from ``agent_runs.tool_calls``, which every run records (and saves while it
runs), so the timeline needs no separate log.
"""

from __future__ import annotations

from typing import Any

from crabagent.core.database import agent_runs_for_session

# Arguments can hold whole files (write content, edit old/new text). Keep enough
# for a readable diff without sending megabytes to the browser.
MAX_ARG_CHARS = 12_000
MAX_STEPS = 2_000
_TRUNCATED = "\n… (truncated)"


def _trim(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= MAX_ARG_CHARS else value[:MAX_ARG_CHARS] + _TRUNCATED
    if isinstance(value, dict):
        return {key: _trim(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_trim(item) for item in value]
    return value


def _step_status(call: dict) -> str:
    if call.get("result_summary") is None:
        return "running"
    return "ok" if call.get("ok", True) else "failed"


async def build_timeline(session_id: str, user_id: int, chat_agent: str = "") -> dict:
    """Runs and their steps for one chat, oldest step first."""
    from crabagent.core.agent.agents import load_agent_registry

    profiles = {a["name"]: a for a in await load_agent_registry()}
    runs = await agent_runs_for_session(session_id, user_id)

    def who(agent_name: str) -> str:
        # The chat's own run is recorded as "main"; show it as the agent the chat talks to.
        return (chat_agent or "assistant") if agent_name == "main" else agent_name

    out_runs: list[dict] = []
    steps: list[dict] = []
    for run in runs:
        if run["agent_name"] == "pipeline":
            continue  # a container for its steps' runs; it calls no tools itself
        agent = who(run["agent_name"])
        profile = profiles.get(agent, {})
        calls = run["tool_calls"]
        out_runs.append(
            {
                "run_id": run["id"],
                "parent_run_id": run["parent_run_id"],
                "agent": agent,
                "display_name": profile.get("display_name") or agent,
                "icon": profile.get("icon") or "",
                "status": run["status"],
                "task": run["task_summary"],
                "started_at": run["started_at"],
                "finished_at": run["finished_at"],
                "elapsed": run["elapsed"],
                "step_count": len(calls),
                "error": run["error"],
                "sub_agent_id": (run.get("metadata") or {}).get("sub_agent_id", ""),
            }
        )
        for index, call in enumerate(calls):
            if not isinstance(call, dict):
                continue
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            steps.append(
                {
                    "run_id": run["id"],
                    "agent": agent,
                    "index": index,
                    "tool": call.get("name", ""),
                    "summary": str(args.get("summary") or ""),
                    "args": _trim({k: v for k, v in args.items() if k != "summary"}),
                    "started_at": call.get("started_at") or run["started_at"],
                    "elapsed": call.get("elapsed") or 0,
                    "status": _step_status(call),
                    "result": call.get("result_summary") or "",
                }
            )
    steps.sort(key=lambda step: (step["started_at"], step["run_id"], step["index"]))
    return {
        "session_id": session_id,
        "running": any(run["status"] == "running" for run in out_runs),
        "runs": out_runs,
        "steps": steps[-MAX_STEPS:],
    }

from __future__ import annotations

import re

# bash ends a finished command's output with "[exit code: N]" when N != 0. Its own
# truncation keeps the tail, so the marker is the last thing in the result. An
# auto-backgrounded command shows "[exit code: -1]" mid-text, followed by the
# background note: it is still running, not failed, so the marker must be at the end.
_BASH_EXIT = re.compile(r"\[exit code: (-?\d+)\]\s*\Z")
# bash_output(pid=...) opens with "[PID 123] exited (exit code N) | ...".
_JOB_EXIT = re.compile(r"\[PID \d+\] exited \(exit code (-?\d+)\)")


def _exit_code(name: str, s: str) -> int | None:
    if name == "bash":
        m = _BASH_EXIT.search(s)
    elif name == "bash_output":
        m = _JOB_EXIT.match(s)
    else:
        return None
    return int(m.group(1)) if m else None


def classify_tool_outcome(name: str, result: object) -> tuple[bool, str]:
    """(ok, reason_class) for one tool result.

    Pass the FULL result: the exit code of a shell command is at the end of its
    output, so a preview cut from the front loses it.
    """
    if not isinstance(result, str):
        return True, 'ok'
    s = result
    # Only the registry's own refusals count, and each one is anchored to the
    # start of the result: "Error: ..." or one of the exact prefixes below. A
    # tool *result* that merely mentions a phrase such as "requires an active
    # session" (reading CrabAgent's own source, say) is a success.
    if s.startswith('Error'):
        if 'requires an active session' in s or 'no user_id in context' in s:
            return False, 'no_session'
        if 'is not available (may be restricted' in s:
            return False, 'unknown_tool'
        return False, 'error'
    # "Tool error: ..." is what the loop records when a handler raises.
    if s.startswith('Tool error:'):
        return False, 'error'
    if s.startswith("Tool '") and 'execution denied by user' in s:
        return False, 'user_denied'
    if s.startswith("Tool '") and 'is disabled (no permission' in s:
        return False, 'permission_denied'
    if s.startswith('Command denied by user:'):
        return False, 'user_denied'
    if s.startswith('Command blocked:'):
        return False, 'blocked'
    code = _exit_code(name, s)
    if name == "run_tests" and s.startswith(("FAILED:", "ERROR:")):
        code = 1
    if code:  # None (no exit code) and 0 are both fine
        # Kept apart from 'error': the tool worked, the command failed. Some of these
        # are expected (a test written to fail first, grep finding nothing).
        return False, 'exit_nonzero'
    return True, 'ok'


async def record_tool_invocations(db, entries: list[dict]) -> None:
    from crabagent.core.database import ToolInvocation

    for e in entries:
        db.add(
            ToolInvocation(
                **{
                    k: e.get(k)
                    for k in (
                        "user_id",
                        "session_id",
                        "run_id",
                        "agent_name",
                        "tool_name",
                        "ok",
                        "reason_class",
                        "latency_ms",
                        "session_mode",
                    )
                }
            )
        )
    await db.commit()


async def aggregate_tool_usage(db, since_days: int = 30) -> list[dict]:
    import datetime

    from sqlalchemy import select

    from crabagent.core.database import ToolInvocation
    from crabagent.core.timeutil import utcnow

    cutoff = utcnow() - datetime.timedelta(days=since_days)
    rows = (await db.execute(select(ToolInvocation).where(ToolInvocation.created_at >= cutoff))).scalars().all()

    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row.tool_name, []).append(row)

    stats = []
    for tool_name, items in groups.items():
        calls = len(items)
        failures = sum(1 for r in items if not r.ok)
        stats.append(
            {
                "tool_name": tool_name,
                "calls": calls,
                "failures": failures,
                "failure_rate": round(failures / calls, 3) if calls else 0.0,
                "last_used": max(r.created_at for r in items),
                "avg_latency_ms": round(sum(r.latency_ms or 0 for r in items) / calls, 1) if calls else 0.0,
            }
        )
    stats.sort(key=lambda s: (-s["calls"], s["tool_name"]))
    return stats

from __future__ import annotations


def classify_tool_outcome(name: str, result: object) -> tuple[bool, str]:
    if not isinstance(result, str):
        return True, 'ok'
    s = result
    if 'requires an active session' in s or 'no user_id in context' in s:
        return False, 'no_session'
    if 'is not available (may be restricted' in s:
        return False, 'unknown_tool'
    if 'is disabled (no permission' in s:
        return False, 'permission_denied'
    if 'execution denied by user' in s:
        return False, 'user_denied'
    if s.startswith('Error'):
        return False, 'error'
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

    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=since_days)
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

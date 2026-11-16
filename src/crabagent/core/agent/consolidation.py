from __future__ import annotations


def verify_consolidation(
    transcripts: list[dict],
    available_tools: set[str],
    replacements: dict[str, str] | None = None,
) -> list[dict]:
    violations = []
    repl = replacements or {}
    for i, t in enumerate(transcripts):
        if not t.get("success"):
            continue
        for name in t.get("tool_calls", []):
            if name in available_tools or name in repl:
                continue
            violations.append({"transcript": i, "tool": name})
    return violations


async def load_transcripts_from_runs(db, limit: int = 100) -> list[dict]:
    import json

    from sqlalchemy import select

    from crabagent.core.database import AgentRun

    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.status == "completed")
        .order_by(AgentRun.id.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    out = []
    for r in rows:
        raw = r.tool_calls
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = None
        names = [c.get("name", "") for c in (raw or []) if isinstance(c, dict)]
        out.append({"tool_calls": names, "success": True})
    return out

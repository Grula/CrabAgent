from __future__ import annotations

_FALLBACK_HINT = "Query ~/.crabagent/crabagent.db read-only (sqlite3 mode=ro) instead."

_ITEMS = [
    {"anti_pattern": "Retrying a tool that returned 'requires an active session' (e.g. agent_runs_inspect, conversation_search, memory_*)", "alternative": "In unattended/scheduled runs these tools always fail; query ~/.crabagent/crabagent.db read-only (sqlite3 mode=ro) instead."},
    {"anti_pattern": "Inventing a tool name that is not in your tool list", "alternative": "Only call tools that are actually listed in your tool list; use tool_search to load more tools."},
    {"anti_pattern": "Assuming write access to the crabagent.db database", "alternative": "Open the database read-only (mode=ro); never write to it from an unattended run."},
    {"anti_pattern": "Dumping all columns of a large table (e.g. agent_runs)", "alternative": "Select only the narrow columns you actually need, to avoid burning tokens."},
    {"anti_pattern": "Retrying the same failed tool call without changing anything", "alternative": "Re-read the error, question your assumption, and try a different approach."},
]


def known_failure_modes(max_items: int = 5) -> list[dict]:
    return _ITEMS[:max(0, min(max_items, 5))]


def inject_failure_modes(system_prompt: str, modes: list[dict]) -> str:
    if not modes:
        return system_prompt
    if "Known failure modes to avoid:" in system_prompt:
        return system_prompt
    lines = ["Known failure modes to avoid:"]
    for m in modes:
        lines.append(f"- Anti-pattern: {m['anti_pattern']}")
        lines.append(f"  Do instead: {m['alternative']}")
    block = "\n".join(lines)
    return system_prompt.rstrip() + "\n\n" + block

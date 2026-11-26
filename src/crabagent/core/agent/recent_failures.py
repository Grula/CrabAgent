"""The latest failing checks of a delegating run, handed to the next coding brief.

A coder's report summarises its failing tests; the architect is supposed to pass
the exact output on, but briefs lose detail. The sub-agent event bridge
(agents.spawn_sub_agent) feeds every ``run_tests`` and ``diagnostics`` result of
its sub-agents into :func:`note_check_result`; the next agent that can edit files
gets :func:`failures_block` appended to its task. A later passing run clears the
entry, so a fixed failure is never handed on.
"""

from __future__ import annotations

import re
import time
from typing import Any

STORE_KEY = "_recent_failures"
MAX_AGE_SECONDS = 30 * 60
MAX_CHARS = 2500
_DIAG_PROBLEMS = re.compile(r"^Checked \d+ files?: (\d+) problems?\.")


def _store(context: Any) -> dict:
    return context.metadata.setdefault(STORE_KEY, {})


def note_check_result(context: Any, agent_name: str, tool_name: str, result: str) -> None:
    """Remember a failing check, or forget the last one of that kind when it passes."""
    text = (result or "").strip()
    kind = None
    failing = False
    if tool_name == "run_tests":
        kind = "tests"
        if text.startswith(("FAILED:", "ERROR:")):
            failing = True
        elif not text.startswith("PASSED:"):
            return  # a usage error, not a test result
    elif tool_name == "diagnostics":
        kind = "diagnostics"
        match = _DIAG_PROBLEMS.match(text)
        if match:
            failing = int(match.group(1)) > 0
        elif not text.startswith("Checked "):
            return
    if kind is None:
        return
    store = _store(context)
    if failing:
        store[kind] = {"agent": agent_name, "at": time.time(), "text": text[:MAX_CHARS]}
    else:
        store.pop(kind, None)


def failures_block(context: Any, task: str, now: float | None = None) -> str:
    """The "recent failures" section for a new coding brief, or "" when there is none.

    Entries older than :data:`MAX_AGE_SECONDS`, or whose first line the brief
    already quotes, are left out.
    """
    store = (getattr(context, "metadata", None) or {}).get(STORE_KEY) or {}
    now = time.time() if now is None else now
    parts = []
    for kind, label in (("tests", "Last failing test run"), ("diagnostics", "Last diagnostics with problems")):
        entry = store.get(kind)
        if not entry or now - entry["at"] > MAX_AGE_SECONDS:
            continue
        first_line = entry["text"].splitlines()[0] if entry["text"] else ""
        if first_line and first_line in (task or ""):
            continue
        minutes = max(1, round((now - entry["at"]) / 60))
        parts.append(f"### {label} (from {entry['agent']}, about {minutes} min ago)\n{entry['text']}")
    if not parts:
        return ""
    return (
        "\n\n## Recent failures in this session (added automatically)\n"
        "The latest failing checks from your teammates' runs, verbatim. Use them if they concern your "
        "task; ignore them if they don't.\n\n" + "\n\n".join(parts)
    )

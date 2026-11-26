"""The agent's own checklist for a long task (``plan_update``).

Kept in the run context, not in the user's todo lists: it's working state for
this run. The list is replaced as a whole on every call, so the model always
states the full current plan. After a context compaction the summary loses the
plan_update calls themselves, so :func:`render_run_plan` is appended to the
compaction summary (see compress.py) and the plan survives.
"""

from __future__ import annotations

from typing import Any

from crabagent.core.agent.tools.registry import registry

RUN_PLAN_KEY = "_run_plan"
_STATUSES = ("pending", "in_progress", "done")
_MARKS = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}
_MAX_ITEMS = 30
_MAX_TEXT = 300


def render_run_plan(context: Any) -> str:
    """The current checklist as text, or "" when the run has none."""
    items = (getattr(context, "metadata", None) or {}).get(RUN_PLAN_KEY) or []
    if not items:
        return ""
    lines = ["## Your task checklist (plan_update)"]
    lines += [f"{_MARKS[item['status']]} {item['text']}" for item in items]
    return "\n".join(lines)


@registry.register(
    name="plan_update",
    description=(
        "Keep your own checklist for a multi-step task: pass the full list every time, each item with its "
        "status (pending, in_progress, done). It is kept through context compaction, so use it for tasks "
        "of more than about three steps; mark an item done as soon as it is, and keep one in_progress."
    ),
    parameters={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": f"The whole checklist, in order (at most {_MAX_ITEMS} items).",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "What the step is, one line."},
                        "status": {"type": "string", "enum": list(_STATUSES)},
                    },
                    "required": ["text", "status"],
                },
            },
        },
        "required": ["items"],
    },
    metadata={"source": "builtin", "category": "planning"},
)
def plan_update(items: list, context: Any = None) -> str:
    if context is None:
        return "Error: plan_update requires a running agent"
    if not isinstance(items, list):
        return "Error: items must be an array of {text, status} objects."
    if len(items) > _MAX_ITEMS:
        return f"Error: at most {_MAX_ITEMS} items; group small steps together."
    clean: list[dict[str, str]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            return f"Error: item {index} must be an object with text and status."
        text = " ".join(str(item.get("text") or "").split())
        status = str(item.get("status") or "").strip()
        if not text:
            return f"Error: item {index} has no text."
        if status not in _STATUSES:
            return f"Error: item {index} has status {status!r}; use one of {', '.join(_STATUSES)}."
        clean.append({"text": text[:_MAX_TEXT], "status": status})
    context.metadata[RUN_PLAN_KEY] = clean
    if not clean:
        return "Checklist cleared."
    done = sum(1 for item in clean if item["status"] == "done")
    return f"Checklist updated ({done}/{len(clean)} done):\n" + render_run_plan(context).split("\n", 1)[1]

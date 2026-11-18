from __future__ import annotations

import datetime
import json
import logging

from crabagent.core.timeutil import from_local, to_local, utcnow

logger = logging.getLogger(__name__)


async def process_meeting_notes(
    notes: str,
    meeting_title: str = "",
    user_id: int = 1,
    context=None,
    session_id: str = "",
) -> dict:
    import litellm

    from crabagent.core.database import async_session_factory
    from crabagent.core.provider_store import (
        get_default_provider,
        resolve_litellm_params,
        resolve_model_for_provider,
    )
    from crabagent.core.task.store import add_task as _add_task

    model = "gpt-4o"
    llm_params: dict = {}
    try:
        provider = await get_default_provider()
        if provider:
            requested = model
            if context:
                requested = context.metadata.get("model", model)
            model = resolve_model_for_provider(provider, requested)
            llm_params = await resolve_litellm_params(provider)
    except Exception:
        pass

    # The model works in the user's local calendar dates, not UTC ones.
    today = to_local(utcnow()).strftime("%Y-%m-%d")
    prompt = (
        f"You are an AI assistant that extracts action items from meeting notes.\n\n"
        f"Today's date: {today}\n\n"
        f"Meeting notes:\n{notes}\n\n"
        f"Extract all action items from these notes. "
        f"For each action item, identify:\n"
        f"- title (required, short description)\n"
        f"- description (optional, longer context)\n"
        f"- assignee (optional, who is responsible)\n"
        f"- deadline (optional, date in YYYY-MM-DD format. If relative like 'next Friday', compute from today={today})\n"
        f"- priority (optional: high/medium/low. Default: medium)\n\n"
        f"Also extract a brief meeting summary if possible.\n\n"
        f"Respond with JSON only, using this exact format:\n"
        f"{{\n"
        f'  "summary": "Brief meeting summary",\n'
        f'  "action_items": [\n'
        f"    {{\n"
        f'      "title": "...",\n'
        f'      "description": "...",\n'
        f'      "assignee": "...",\n'
        f'      "deadline": "YYYY-MM-DD",\n'
        f'      "priority": "medium"\n'
        f"    }}\n"
        f"  ]\n"
        f"}}\n\n"
        f"If there are no action items, set action_items to an empty array.\n"
        f"IMPORTANT: Output ONLY valid JSON, no other text."
    )

    try:
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000,
            temperature=0.1,
            **llm_params,
        )
        raw = response.choices[0].message.content or "{}"
    except Exception as e:
        logger.error(f"Meeting LLM call failed: {e}")
        return {
            "summary": "Failed to process meeting notes.",
            "action_items": [],
            "created_tasks": [],
        }

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                cleaned = cleaned[start : end + 1]
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = {"summary": "Extraction completed.", "action_items": []}

    summary = data.get("summary", "Meeting processed.")
    action_items = data.get("action_items", [])

    created_tasks = []
    async with async_session_factory() as db:
        for item in action_items:
            title = item.get("title", "").strip()
            if not title:
                continue
            deadline = None
            dl_str = item.get("deadline", "")
            if dl_str:
                try:
                    # A date-only deadline names a LOCAL day; storage is UTC.
                    deadline = from_local(datetime.datetime.strptime(dl_str[:10], "%Y-%m-%d"))
                except ValueError:
                    pass
            try:
                t = await _add_task(
                    db,
                    user_id=user_id,
                    title=title,
                    description=item.get("description", ""),
                    assignee=item.get("assignee", ""),
                    deadline=deadline,
                    source="meeting",
                    source_ref=meeting_title or today,
                    source_session=session_id,
                    priority=item.get("priority", "medium"),
                )
                created_tasks.append(t)
            except Exception as e:
                logger.error(f"Failed to create task: {e}")

    return {
        "summary": summary,
        "action_items": action_items,
        "created_tasks": created_tasks,
    }


def _deadline_text(dl) -> str:
    """Render a task deadline in the local frame for display.

    Deadlines are stored as naive UTC; the store returns a date-only deadline as
    a bare local ``YYYY-MM-DD`` string, which is already display-ready.
    """
    if isinstance(dl, datetime.datetime):
        return to_local(dl).strftime("%Y-%m-%d")
    text = str(dl)
    if len(text) > 10 and "T" in text:
        try:
            return to_local(datetime.datetime.fromisoformat(text)).strftime("%Y-%m-%d")
        except ValueError:
            return text[:10]
    return text[:10]


def format_meeting_result(result: dict, meeting_title: str = "") -> str:
    created_tasks = result.get("created_tasks", []) or []
    summary = result.get("summary", "Meeting processed.")

    lines = [
        f"📋 **{meeting_title or 'Meeting Notes'}**",
        "",
        f"📝 {summary}",
        "",
    ]

    if created_tasks:
        lines.append(f"✅ **{len(created_tasks)} action items created:**")
        lines.append("")
        for i, t in enumerate(created_tasks, 1):
            pri_dot = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
                t.get("priority", "medium"), "🟡"
            )
            line = f"  {i}. {pri_dot} **{t['title']}** (task #{t['id']})"
            if t.get("assignee"):
                line += f" 👤 {t['assignee']}"
            if t.get("deadline"):
                line += f" 📅 {_deadline_text(t['deadline'])}"
            lines.append(line)
            if t.get("description"):
                lines.append(f"     {t['description'][:120]}")
    else:
        lines.append("📭 No action items found in the notes.")

    return "\n".join(lines)

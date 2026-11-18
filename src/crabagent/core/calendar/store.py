"""Calendar event CRUD + aggregated queries (events + task deadlines).

All functions are async and take an ``AsyncSession``.  Task-derived
events are generated dynamically (not persisted) so they always reflect
the latest task state.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crabagent.core.database import CalendarEvent
from crabagent.core.timeutil import as_utc, from_local, iso_utc, to_local, utcnow

logger = logging.getLogger(__name__)


def _event_to_dict(e: CalendarEvent) -> dict:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "title": e.title,
        "description": e.description,
        "start_time": iso_utc(e.start_time),
        "end_time": iso_utc(e.end_time),
        "all_day": e.all_day,
        "type": e.type,
        "source": e.source,
        "linked_task_id": e.linked_task_id,
        "project": e.project,
        "location": e.location,
        "color": e.color,
        "reminder_minutes": e.reminder_minutes,
        "reminder_sent": e.reminder_sent,
        "ical_uid": e.ical_uid,
        "ical_source_id": e.ical_source_id,
        "created_at": iso_utc(e.created_at),
        "updated_at": iso_utc(e.updated_at),
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def add_event(
    db: AsyncSession,
    user_id: int,
    title: str,
    start_time: datetime.datetime,
    end_time: datetime.datetime | None = None,
    all_day: bool = False,
    description: str = "",
    location: str = "",
    project: str = "",
    event_type: str = "manual",
    source: str = "manual",
    linked_task_id: int | None = None,
    reminder_minutes: int = 15,
    color: str = "",
    ical_uid: str = "",
    ical_source_id: int | None = None,
) -> dict:
    e = CalendarEvent(
        user_id=user_id,
        title=title,
        description=description,
        start_time=start_time,
        end_time=end_time,
        all_day=all_day,
        type=event_type,
        source=source,
        linked_task_id=linked_task_id,
        project=project,
        location=location,
        color=color,
        reminder_minutes=reminder_minutes,
        ical_uid=ical_uid,
        ical_source_id=ical_source_id,
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return _event_to_dict(e)


async def get_event(db: AsyncSession, event_id: int, user_id: int) -> dict | None:
    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.id == event_id,
            CalendarEvent.user_id == user_id,
        )
    )
    e = result.scalar_one_or_none()
    return _event_to_dict(e) if e else None


async def update_event(
    db: AsyncSession,
    event_id: int,
    user_id: int,
    **kwargs,
) -> dict | None:
    allowed = {
        "title", "description", "start_time", "end_time", "all_day",
        "type", "source", "linked_task_id", "project", "location",
        "color", "reminder_minutes", "reminder_sent",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return await get_event(db, event_id, user_id)
    updates["updated_at"] = utcnow()
    from sqlalchemy import update as sa_update

    stmt = (
        sa_update(CalendarEvent)
        .where(CalendarEvent.id == event_id, CalendarEvent.user_id == user_id)
        .values(**updates)
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        return None
    await db.commit()
    return await get_event(db, event_id, user_id)


async def delete_event(db: AsyncSession, event_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.id == event_id,
            CalendarEvent.user_id == user_id,
        )
    )
    e = result.scalar_one_or_none()
    if not e:
        return False
    await db.delete(e)
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Aggregated queries
# ---------------------------------------------------------------------------

async def list_events(
    db: AsyncSession,
    user_id: int,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[dict]:
    """Return events in [start, end), merged with task-deadline pseudo-events."""
    # 1. Persisted events
    stmt = (
        select(CalendarEvent)
        .where(
            CalendarEvent.user_id == user_id,
            CalendarEvent.start_time >= start,
            CalendarEvent.start_time < end,
        )
        .order_by(CalendarEvent.start_time)
    )
    result = await db.execute(stmt)
    events = [_event_to_dict(e) for e in result.scalars().all()]

    # 2. Task-deadline pseudo-events (dynamic, not persisted)
    try:
        from crabagent.core.database import Task
        from crabagent.core.task.status import OPEN_TASK_STATUSES

        task_stmt = (
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.deadline.isnot(None),
                Task.deadline >= start,
                Task.deadline < end,
                Task.status.in_(OPEN_TASK_STATUSES),
            )
            .order_by(Task.deadline)
        )
        task_result = await db.execute(task_stmt)
        for t in task_result.scalars().all():
            # All-day is decided in the LOCAL frame: a date-only deadline is
            # stored as the UTC instant of local midnight, which is not 00:00
            # once the host is not on UTC.
            local_deadline = to_local(t.deadline)
            is_date_only = (
                local_deadline is not None
                and local_deadline.hour == 0
                and local_deadline.minute == 0
                and local_deadline.second == 0
                and local_deadline.microsecond == 0
            )
            end_wire = None
            if is_date_only:
                # Same convention as before: an all-day entry ends at 23:59 local.
                local_end = local_deadline.replace(hour=23, minute=59, second=0, microsecond=0)
                end_wire = iso_utc(from_local(local_end))
            events.append({
                "id": f"task_{t.id}",
                "title": f"⏰ {t.title}",
                "description": t.description or "",
                # Same wire format as the persisted events above (naive UTC
                # with a trailing Z); the caller renders it in the local frame.
                "start_time": iso_utc(t.deadline),
                "end_time": end_wire,
                "all_day": is_date_only,
                "type": "task",
                "source": "task",
                "linked_task_id": t.id,
                "project": t.project,
                "location": "",
                "color": "",
                "priority": t.priority,
                "status": t.status,
            })
    except Exception as e:
        logger.debug("Task deadline merge skipped: %s", e)

    # 3. Sort by start_time
    events.sort(key=lambda e: e.get("start_time", ""))
    return events


async def get_today_overview(db: AsyncSession, user_id: int) -> dict:
    """Return today's events, due tasks, and free time slots."""
    # "Today" is the user's local calendar day; the stored values are UTC.
    local_now = to_local(utcnow())
    today_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = from_local(today_start_local)
    tomorrow_start = from_local(today_start_local + datetime.timedelta(days=1))

    events = await list_events(db, user_id, today_start, tomorrow_start)

    # Separate timed events (for free-slot calculation) from all-day
    timed_events = [e for e in events if not e.get("all_day") and e.get("end_time")]
    all_day_events = [e for e in events if e.get("all_day")]

    # Calculate free slots (9:00–18:00 work hours)
    busy = []
    for e in timed_events:
        start_str = e["start_time"]
        end_str = e["end_time"]
        try:
            s = as_utc(datetime.datetime.fromisoformat(start_str))
            en = as_utc(datetime.datetime.fromisoformat(end_str))
            busy.append((s, en))
        except Exception:
            pass

    work_start = from_local(today_start_local.replace(hour=9))
    work_end = from_local(today_start_local.replace(hour=18))
    free_slots = _calculate_free_time(busy, work_start, work_end)

    # Tasks due today (any type=="task" event)
    due_tasks = [e for e in events if e.get("type") == "task"]

    return {
        "date": today_start_local.date().isoformat(),
        "events": events,
        "timed_events": len(timed_events),
        "all_day_events": len(all_day_events),
        "due_tasks": due_tasks,
        "free_slots": free_slots,
        "summary": (
            f"{len(timed_events)} event(s) today, {len(due_tasks)} task(s) due, "
            f"{len(free_slots)} free slot(s)"
        ),
    }


def _calculate_free_time(
    busy: list[tuple[datetime.datetime, datetime.datetime]],
    day_start: datetime.datetime,
    day_end: datetime.datetime,
) -> list[dict]:
    """Calculate free time slots given busy intervals within work hours."""
    if not busy:
        return [{"start": iso_utc(day_start), "end": iso_utc(day_end)}]

    # Sort by start time
    busy.sort(key=lambda x: x[0])

    slots: list[dict] = []
    cursor = day_start

    for b_start, b_end in busy:
        # Clamp to work hours
        b_start = max(b_start, day_start)
        b_end = min(b_end, day_end)
        if b_start > cursor:
            slots.append({
                "start": iso_utc(cursor),
                "end": iso_utc(b_start),
                "duration_min": int((b_start - cursor).total_seconds() / 60),
            })
        cursor = max(cursor, b_end)

    if cursor < day_end:
        slots.append({
            "start": iso_utc(cursor),
            "end": iso_utc(day_end),
            "duration_min": int((day_end - cursor).total_seconds() / 60),
        })

    return slots


# ---------------------------------------------------------------------------
# Calendar settings (stored in app_settings as JSON)
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS = {
    "morning_brief_enabled": False,
    "morning_brief_time": "08:00",
    "morning_brief_channel": "email",
    "event_reminder_enabled": True,
    "default_reminder_minutes": 15,  # used for iCal-synced events
}


async def load_calendar_settings(db: AsyncSession) -> dict:
    """Load calendar settings from app_settings."""
    import json

    from crabagent.core.database import AppSetting

    result = await db.execute(
        select(AppSetting).where(AppSetting.key == "calendar_settings")
    )
    row = result.scalar_one_or_none()
    if row and row.value:
        try:
            saved = json.loads(row.value)
            return {**_DEFAULT_SETTINGS, **saved}
        except json.JSONDecodeError:
            pass
    return dict(_DEFAULT_SETTINGS)


async def save_calendar_settings(db: AsyncSession, settings: dict) -> dict:
    """Save calendar settings to app_settings."""
    import json

    from crabagent.core.database import AppSetting

    merged = {**_DEFAULT_SETTINGS, **settings}
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == "calendar_settings")
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = json.dumps(merged, ensure_ascii=False)
    else:
        db.add(AppSetting(key="calendar_settings", value=json.dumps(merged, ensure_ascii=False)))
    await db.commit()
    return merged

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.calendar import tools as calendar_tools


def _get_tool(registry: ToolRegistry, name: str):
    tool = registry.get(name)
    assert tool is not None
    return tool.handler


@pytest.mark.asyncio
async def test_calendar_add_rejects_bad_time(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    calendar_tools.register_calendar_tools(registry)
    handler = _get_tool(registry, "calendar_add")

    result = await handler(title="Meeting", start_time="bad-time")

    assert "Could not parse the time" in result


@pytest.mark.asyncio
async def test_calendar_add_marks_date_only_as_all_day(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    calendar_tools.register_calendar_tools(registry)
    handler = _get_tool(registry, "calendar_add")
    captured = {}

    async def fake_add_event(db, **kwargs):
        captured.update(kwargs)
        return {"id": 1, "title": kwargs["title"]}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.calendar.store.add_event", fake_add_event)
    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())

    result = await handler(title="Day off", start_time="2026-07-01")

    assert captured["all_day"] is True
    assert "Event created" in result


@pytest.mark.asyncio
async def test_calendar_today_formats_events_and_free_slots(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    calendar_tools.register_calendar_tools(registry)
    handler = _get_tool(registry, "calendar_today")

    overview = {
        "date": "2026-07-01",
        "summary": "2 events",
        "events": [
            {"title": "Standup", "start_time": "2026-07-01T09:00:00", "end_time": "2026-07-01T10:00:00", "all_day": False, "type": "manual", "location": "A1"},
            {"title": "Deadline", "start_time": "2026-07-01T00:00:00", "end_time": None, "all_day": True, "type": "task", "location": ""},
        ],
        "free_slots": [{"start": "2026-07-01T13:00:00", "end": "2026-07-01T15:30:00", "duration_min": 150}],
    }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.calendar.store.get_today_overview", _async_return(overview))
    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())

    result = await handler()

    assert "Standup" in result
    assert "13:00-15:30 (2h30m)" in result
    assert "All-day / due" in result


@pytest.mark.asyncio
async def test_calendar_update_requires_changed_fields(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    calendar_tools.register_calendar_tools(registry)
    handler = _get_tool(registry, "calendar_update")

    result = await handler(id=1)

    assert result == "⚠️ No fields to update."


@pytest.mark.asyncio
async def test_calendar_delete_returns_missing_message(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    calendar_tools.register_calendar_tools(registry)
    handler = _get_tool(registry, "calendar_delete")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.calendar.store.delete_event", _async_return(False))
    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())

    result = await handler(id=4)

    assert result == "❌ Event 4 not found."


def test_parse_helpers_cover_valid_and_invalid_inputs():
    assert calendar_tools._parse_datetime("2026-07-01 09:15") == datetime.datetime(2026, 7, 1, 9, 15)
    assert calendar_tools._parse_datetime("2026-07-01T09:15") == datetime.datetime(2026, 7, 1, 9, 15)
    assert calendar_tools._parse_datetime("bad") is None
    assert calendar_tools._parse_date("2026-07-01") == datetime.datetime(2026, 7, 1, 0, 0)
    assert calendar_tools._parse_date("bad") is None


def _async_return(value):
    async def inner(*args, **kwargs):
        return value

    return inner

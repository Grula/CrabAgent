from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.task import tools as task_tools
from crabagent.core.timeutil import from_local, iso_utc


def _get_tool(registry: ToolRegistry, name: str):
    tool = registry.get(name)
    assert tool is not None
    return tool.handler


@pytest.mark.asyncio
async def test_task_add_parses_datetime_deadline(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    handler = _get_tool(registry, "task_add")
    captured = {}

    async def fake_add(db, **kwargs):
        captured.update(kwargs)
        return {"id": 12, "title": kwargs["title"]}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.task.store.add_task", fake_add)
    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())

    context = SimpleNamespace(metadata={"user_id": 7, "session_id": "sess-abc"})
    result = await handler(
        title="Write report",
        deadline="2026-07-02 10:30",
        assignee="xcl",
        project="Crab",
        priority="high",
        context=context,
    )

    # Storage is naive UTC: the local wall clock 10:30 is converted on write.
    assert captured["deadline"] == from_local(datetime.datetime(2026, 7, 2, 10, 30))
    assert captured["deadline"].tzinfo is None
    # ...and the text shown back to the model/user keeps the local 10:30.
    assert "📅 2026-07-02 10:30" in result
    assert "id=12" in result
    assert "🏷️ high" in result


@pytest.mark.asyncio
async def test_task_add_accepts_iso_t_deadline(monkeypatch: pytest.MonkeyPatch):
    """The API path and task_update take the ISO T form; task_add must too."""
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    handler = _get_tool(registry, "task_add")
    captured = {}

    async def fake_add(db, **kwargs):
        captured.update(kwargs)
        return {"id": 21, "title": kwargs["title"]}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.task.store.add_task", fake_add)
    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())

    result = await handler(title="Ship report", deadline="2026-11-20T14:30")

    # Stored as naive UTC, exactly like the space-separated form.
    assert captured["deadline"] == from_local(datetime.datetime(2026, 11, 20, 14, 30))
    assert captured["deadline"].tzinfo is None
    # The echo stays the local wall clock the caller supplied.
    assert "📅 2026-11-20T14:30" in result


@pytest.mark.asyncio
async def test_task_add_does_not_echo_unparsed_deadline(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    handler = _get_tool(registry, "task_add")
    captured = {}

    async def fake_add(db, **kwargs):
        captured.update(kwargs)
        return {"id": 22, "title": kwargs["title"]}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.task.store.add_task", fake_add)
    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())

    result = await handler(title="Ship report", deadline="next friday")

    assert captured["deadline"] is None
    assert "📅" not in result
    assert "next friday" not in result


@pytest.mark.asyncio
async def test_task_add_records_source_session_and_agent_source(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    handler = _get_tool(registry, "task_add")
    captured = {}

    async def fake_add(db, **kwargs):
        captured.update(kwargs)
        return {"id": 13, "title": kwargs["title"]}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.task.store.add_task", fake_add)
    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())

    context = SimpleNamespace(metadata={"user_id": 3, "session_id": "sess-xyz"})
    await handler(title="Follow up with customer", context=context)

    assert captured["source"] == "agent"
    assert captured["source_session"] == "sess-xyz"
    assert captured["user_id"] == 3
    assert captured["owner_type"] == "agent"
    assert captured["owner_name"] == "CrabAgent"


@pytest.mark.asyncio
async def test_task_add_defaults_source_session_without_context(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    handler = _get_tool(registry, "task_add")
    captured = {}

    async def fake_add(db, **kwargs):
        captured.update(kwargs)
        return {"id": 14, "title": kwargs["title"]}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.task.store.add_task", fake_add)
    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())

    await handler(title="Task without context")

    assert captured["source"] == "agent"
    assert captured["source_session"] == ""
    assert captured["user_id"] == 1
    assert captured["owner_type"] == "agent"
    assert captured["owner_name"] == "CrabAgent"


@pytest.mark.asyncio
async def test_task_list_formats_empty_and_populated_results(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    handler = _get_tool(registry, "task_list")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())
    monkeypatch.setattr("crabagent.core.task.store.list_tasks", _async_return([]))
    empty = await handler(status="pending", project="Crab")
    assert "No pending tasks found for project" in empty

    monkeypatch.setattr(
        "crabagent.core.task.store.list_tasks",
        _async_return(
            [
                {
                    "id": 1,
                    "status": "pending",
                    "priority": "high",
                    "title": "Fix bug",
                    "deadline": "2026-07-02T00:00:00",
                    "project": "Crab",
                    "assignee": "xcl",
                    "description": "A" * 100,
                }
            ]
        ),
    )
    filled = await handler(status="pending")
    assert "Fix bug" in filled
    assert "👤 xcl" in filled
    assert "…" in filled


@pytest.mark.asyncio
async def test_task_list_renders_deadline_in_local_time(monkeypatch: pytest.MonkeyPatch):
    """A timed deadline is stored as naive UTC but listed on the local day."""
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    handler = _get_tool(registry, "task_list")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    # 23:30 on the user's local 2026-07-02 is already 2026-07-03 in UTC on hosts
    # west of Greenwich, so a raw UTC slice would print the wrong day.
    stored = from_local(datetime.datetime(2026, 7, 2, 23, 30))
    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())
    monkeypatch.setattr(
        "crabagent.core.task.store.list_tasks",
        _async_return(
            [
                {
                    "id": 2,
                    "status": "pending",
                    "priority": "medium",
                    "title": "Ship release",
                    "deadline": iso_utc(stored),
                    "project": "",
                    "assignee": "",
                    "description": "",
                }
            ]
        ),
    )

    listed = await handler(status="pending")

    assert "📅2026-07-02" in listed
    utc_day = iso_utc(stored)[:10]
    if utc_day != "2026-07-02":  # hosts west of UTC: the raw UTC day must not leak
        assert utc_day not in listed, "the raw UTC day leaked into the local task listing"


@pytest.mark.asyncio
async def test_task_done_and_delete_report_missing(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    done_handler = _get_tool(registry, "task_done")
    delete_handler = _get_tool(registry, "task_delete")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())
    monkeypatch.setattr("crabagent.core.task.store.update_task", _async_return(None))
    monkeypatch.setattr("crabagent.core.task.store.delete_task", _async_return(False))

    assert await done_handler(id=99) == "❌ Task 99 not found."
    assert await delete_handler(id=99) == "❌ Task 99 not found."


@pytest.mark.asyncio
async def test_task_update_reports_changed_fields(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    handler = _get_tool(registry, "task_update")
    captured = {}

    async def fake_update(db, task_id, user_id, **kwargs):
        captured.update(kwargs)
        return {"title": "Fix bug"}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())
    monkeypatch.setattr("crabagent.core.task.store.update_task", fake_update)

    result = await handler(id=1, deadline="2026-07-02T10:30", status="in_progress", priority="low")

    # The store takes naive UTC; the tool keeps the local render in its text.
    assert captured["deadline"] == from_local(datetime.datetime(2026, 7, 2, 10, 30))
    assert captured["deadline"].tzinfo is None
    assert "deadline, status, priority" in result


@pytest.mark.asyncio
async def test_task_update_reports_not_found(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    handler = _get_tool(registry, "task_update")

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())
    monkeypatch.setattr("crabagent.core.task.store.update_task", _async_return(None))

    result = await handler(id=1, title="new")

    assert result == "❌ Task 1 not found."


@pytest.mark.asyncio
async def test_task_done_mid_run_defers_card(monkeypatch: pytest.MonkeyPatch):
    """task_done during an active run must NOT broadcast a premature card."""
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    done_handler = _get_tool(registry, "task_done")
    broadcasts: list[tuple] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_update(db, task_id, user_id, **kwargs):
        return {"id": 1, "title": "Write report", "active_run_id": 42, "source_session": "s1"}

    import crabagent.core.task.events as task_events

    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())
    monkeypatch.setattr("crabagent.core.task.store.update_task", fake_update)
    monkeypatch.setattr(task_events, "broadcast_task_event", lambda t, d: broadcasts.append((t, d)))

    result = await done_handler(id=1)

    assert "Completion request recorded" in result
    assert broadcasts == []


@pytest.mark.asyncio
async def test_task_done_without_active_run_broadcasts(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()
    task_tools.register_task_tools(registry)
    done_handler = _get_tool(registry, "task_done")
    broadcasts: list[tuple] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_update(db, task_id, user_id, **kwargs):
        return {"id": 1, "title": "Write report", "active_run_id": None, "source_session": "s1"}

    import crabagent.core.task.events as task_events

    monkeypatch.setattr("crabagent.core.database.async_session_factory", lambda: FakeSession())
    monkeypatch.setattr("crabagent.core.task.store.update_task", fake_update)
    monkeypatch.setattr(task_events, "broadcast_task_event", lambda t, d: broadcasts.append((t, d)))

    result = await done_handler(id=1)

    assert "marked as done" in result
    assert len(broadcasts) == 1
    assert broadcasts[0][1]["status"] == "done"
    assert broadcasts[0][1]["session_id"] == "s1"


def _async_return(value):
    async def inner(*args, **kwargs):
        return value

    return inner

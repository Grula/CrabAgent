"""RED tests for B1 — meeting notes extraction core.

Module under test (to be created): ``crabagent.core.meeting.processor``
    async def process_meeting_notes(notes, meeting_title="", user_id=1, context=None, session_id="") -> dict
    def format_meeting_result(result, meeting_title="") -> str

Only ``litellm.acompletion`` and ``crabagent.core.task.store.add_task`` (plus the DB
session factory) are faked, so no network call and no real database is touched.
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest

import crabagent.core.database as db_module
import crabagent.core.task.store as task_store
from crabagent.core.meeting import processor


class FakeSession:
    """Stand-in for an AsyncSession context manager."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _fake_session_factory():
    return FakeSession()


class FakeLLM:
    """Stand-in for ``litellm.acompletion`` (records the call, returns content or raises)."""

    def __init__(self, content: str = "", exc: Exception | None = None):
        self.content = content
        self.exc = exc
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _wire(monkeypatch: pytest.MonkeyPatch, llm: FakeLLM, calls: list[dict]):
    """Wire the LLM + session factory + add_task, tolerating either import style.

    The processor may import ``add_task`` / ``async_session_factory`` at module level
    or inside the function; patch both the definition site and the processor module
    attribute when it exists so the tests do not depend on that choice.
    """

    async def fake_add(db=None, **kwargs):
        # Mirror the real task_store.add_task return value (_task_to_dict):
        # deadline is serialized to ISO text, not a datetime.
        deadline = kwargs.get("deadline")
        record = {
            "id": 100 + len(calls),
            "title": kwargs.get("title", ""),
            "description": kwargs.get("description", ""),
            "assignee": kwargs.get("assignee", ""),
            "priority": kwargs.get("priority", "medium"),
            "deadline": deadline.isoformat() if isinstance(deadline, datetime.datetime) else deadline,
            "source": kwargs.get("source", ""),
            "source_session": kwargs.get("source_session", ""),
        }
        calls.append(kwargs)
        return record

    monkeypatch.setattr("litellm.acompletion", llm)
    monkeypatch.setattr(db_module, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(task_store, "add_task", fake_add)
    if hasattr(processor, "add_task"):
        monkeypatch.setattr(processor, "add_task", fake_add, raising=False)
    if hasattr(processor, "async_session_factory"):
        monkeypatch.setattr(processor, "async_session_factory", _fake_session_factory, raising=False)
    return fake_add


# ---------------------------------------------------------------------------
# R1 — result shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_result_has_summary_action_items_and_created_tasks(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []
    llm = FakeLLM(json.dumps({"summary": "Short sync", "action_items": []}))
    _wire(monkeypatch, llm, calls)

    result = await processor.process_meeting_notes("we talked", meeting_title="Standup", session_id="sess-1")

    assert isinstance(result, dict)
    assert set(result) >= {"summary", "action_items", "created_tasks"}
    assert result["summary"] == "Short sync"
    assert isinstance(result["summary"], str)
    assert result["action_items"] == []
    assert result["created_tasks"] == []
    assert calls == []


# ---------------------------------------------------------------------------
# R2 — one action item -> one task created with all metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_action_item_creates_task_with_metadata(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []
    payload = {
        "summary": "S",
        "action_items": [
            {
                "title": "T",
                "description": "D",
                "assignee": "alice",
                "deadline": "2026-11-20",
                "priority": "high",
            }
        ],
    }
    llm = FakeLLM(json.dumps(payload))
    _wire(monkeypatch, llm, calls)

    result = await processor.process_meeting_notes("notes", meeting_title="Weekly", session_id="sess-42")

    assert result["summary"] == "S"
    assert len(result["action_items"]) == 1

    assert len(calls) == 1, f"expected exactly one add_task call, got {calls}"
    created = calls[0]
    assert created["title"] == "T"
    assert created["assignee"] == "alice"
    assert created["priority"] == "high"
    assert created["source"] == "meeting"
    assert created["source_session"] == "sess-42"
    assert isinstance(created["deadline"], datetime.datetime), (
        f"deadline must be parsed to a datetime, got {created['deadline']!r}"
    )
    assert created["deadline"] == datetime.datetime(2026, 11, 20)

    assert len(result["created_tasks"]) == 1
    assert result["created_tasks"][0]["title"] == "T"


# ---------------------------------------------------------------------------
# R3 — blank titles are skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blank_titles_are_skipped(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []
    payload = {
        "summary": "S",
        "action_items": [
            {"title": "", "assignee": "nobody"},
            {"title": "   ", "assignee": "nobody"},
            {"title": "Real work"},
        ],
    }
    llm = FakeLLM(json.dumps(payload))
    _wire(monkeypatch, llm, calls)

    result = await processor.process_meeting_notes("notes", session_id="sess-3")

    assert len(calls) == 1, f"blank-titled items must not create tasks, got {calls}"
    assert calls[0]["title"] == "Real work"
    assert len(result["created_tasks"]) == 1


# ---------------------------------------------------------------------------
# R4 — LLM failure is reported, not raised
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_failure_returns_failure_summary(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []
    llm = FakeLLM(exc=RuntimeError("provider down"))
    _wire(monkeypatch, llm, calls)

    result = await processor.process_meeting_notes("notes", session_id="sess-4")

    assert result["summary"] == "Failed to process meeting notes."
    assert result["action_items"] == []
    assert result["created_tasks"] == []
    assert calls == []


# ---------------------------------------------------------------------------
# R5 — markdown-fenced JSON is parsed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fenced_json_is_parsed(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []
    fenced = '```json\n{"summary": "Fenced summary", "action_items": [{"title": "Fenced task"}]}\n```'
    llm = FakeLLM(fenced)
    _wire(monkeypatch, llm, calls)

    result = await processor.process_meeting_notes("notes", session_id="sess-5")

    assert result["summary"] == "Fenced summary"
    assert len(calls) == 1
    assert calls[0]["title"] == "Fenced task"


# ---------------------------------------------------------------------------
# R6 — markdown rendering
# ---------------------------------------------------------------------------

def test_format_meeting_result_with_created_tasks():
    result = {
        "summary": "Budget approved.",
        "action_items": [{"title": "Ship it"}],
        "created_tasks": [
            {
                "id": 7,
                "title": "Ship it",
                "assignee": "alice",
                "priority": "high",
                "deadline": "2026-11-20T00:00:00",  # add_task returns ISO text
            }
        ],
    }

    text = processor.format_meeting_result(result, "Weekly sync")

    assert isinstance(text, str)
    assert "Weekly sync" in text
    assert "Budget approved." in text
    assert "1 action items created:" in text
    assert "No action items found in the notes." not in text


def test_format_meeting_result_without_created_tasks():
    result = {"summary": "Nothing to do.", "action_items": [], "created_tasks": []}

    text = processor.format_meeting_result(result, "Weekly sync")

    assert "Weekly sync" in text
    assert "Nothing to do." in text
    assert "No action items found in the notes." in text
    assert "action items created:" not in text

"""RED tests for M1 — persist meeting minutes on end.

Contract under test (implementer makes these true):
1. ``database.MeetingSession`` gains ``minutes`` (Text, default "").
3. ``meeting_session_upsert(..., minutes: str = "")`` stores ``row.minutes = minutes``
   (keyword param).
4. ``api.meeting._meeting_to_dict`` includes ``"minutes": row.minutes or ""``.
5. ``api.meeting.end_meeting_session`` persists the generated summary into the
   row's ``minutes`` column and returns it as ``body["minutes"]``.

``processor.process_meeting_notes`` is faked so no LLM call happens.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db
from crabagent.core.database import MeetingSession, meeting_session_get, meeting_session_upsert
from crabagent.core.meeting import processor
from crabagent.serve.api.meeting import _meeting_to_dict, router
from crabagent.serve.deps import get_current_user

MINUTES = "Budget approved. Bob ships it."
NOTES = [
    {"timestamp": "10:00", "speaker": "Alice", "content": "Budget review"},
    {"timestamp": "10:05", "speaker": "Bob", "content": "Ship it"},
]


@pytest.fixture
async def session_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


# ---------------------------------------------------------------------------
# Schema (contract 1)
# ---------------------------------------------------------------------------

def test_meeting_session_has_minutes_column():
    columns = MeetingSession.__table__.c

    assert "minutes" in columns, f"meeting_sessions has no minutes column: {list(columns)}"


# ---------------------------------------------------------------------------
# upsert (contract 3)
# ---------------------------------------------------------------------------

async def test_upsert_persists_minutes(session_db):
    row = await meeting_session_upsert(session_db, "s1", 1, minutes=MINUTES)

    assert row.minutes == MINUTES

    fetched = await meeting_session_get(session_db, "s1", 1)
    assert fetched is not None
    assert fetched.minutes == MINUTES


async def test_upsert_without_minutes_leaves_it_empty(session_db):
    """Safety edge: the default must be empty, never a stale/garbage value."""
    row = await meeting_session_upsert(session_db, "s2", 1, title="No minutes yet")

    assert (row.minutes or "") == ""


# ---------------------------------------------------------------------------
# API serialisation (contract 4)
# ---------------------------------------------------------------------------

def test_meeting_to_dict_includes_minutes():
    row = SimpleNamespace(
        session_id="s1",
        title="Weekly sync",
        participants="[]",
        notes="[]",
        status="ended",
        created_at=None,
        updated_at=None,
        minutes=MINUTES,
    )

    assert _meeting_to_dict(row)["minutes"] == MINUTES

    # NULL / missing-safe projection
    row.minutes = None
    assert _meeting_to_dict(row)["minutes"] == ""


# ---------------------------------------------------------------------------
# end endpoint (contract 5)
# ---------------------------------------------------------------------------

@pytest.fixture
async def client(tmp_path):
    """ASGI client whose DB is a throwaway file; the factory is exposed so the
    test can read the persisted row without going through the serializer."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'meeting_minutes.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def database():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[db.get_db] = database
    app.dependency_overrides[get_current_user] = lambda: db.User(id=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, factory
    await engine.dispose()


async def test_end_persists_summary(client, monkeypatch: pytest.MonkeyPatch):
    async def fake_process_meeting_notes(
        notes="", meeting_title="", user_id=1, context=None, session_id="", **extra
    ):
        return {
            "summary": "Test minutes",
            "action_items": [],
            "created_tasks": [],
        }

    monkeypatch.setattr(processor, "process_meeting_notes", fake_process_meeting_notes)

    c, factory = client
    await c.put(
        "/meeting/m-1",
        json={"title": "Weekly sync", "participants": ["Alice", "Bob"], "notes": NOTES},
    )

    response = await c.post("/meeting/m-1/end", json={"title": "Weekly sync", "participants": [], "notes": NOTES})

    assert response.status_code == 200
    body = response.json()
    # contract 5: the generated summary is returned under "minutes"
    assert (body.get("minutes") or "") == "Test minutes"

    # contract 5: and it is actually persisted on the row
    async with factory() as s:
        result = await s.execute(
            select(MeetingSession).where(MeetingSession.session_id == "m-1")
        )
        stored = result.scalar_one()
        assert stored.status == "ended"
        assert stored.minutes == "Test minutes"

    # a subsequent GET must see the same minutes (nothing transient)
    state = (await c.get("/meeting/m-1")).json()
    assert state["minutes"] == "Test minutes"

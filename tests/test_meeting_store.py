"""RED tests for B2 — MeetingSession model + persistence helpers.

Under test (to be added to ``crabagent.core.database``):
    class MeetingSession(Base)                 # __tablename__ == "meeting_sessions"
    async def meeting_session_get(db, session_id, user_id) -> MeetingSession | None
    async def meeting_session_upsert(db, session_id, user_id, title, participants, notes, status="active")

``participants`` and ``notes`` are stored JSON-encoded (Text) and must round-trip.
Every read is scoped to the owning ``user_id``: a session created by one user is
invisible to another.
Reads are decoded with :func:`_decoded` so the assertions hold whether the stored
value is still JSON text (per spec) or has already been decoded.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core.database import (
    Base,
    Conversation,
    MeetingSession,
    meeting_session_get,
    meeting_session_upsert,
)

PARTICIPANTS = ["alice", "bob"]
NOTES = [
    {"timestamp": "10:00", "speaker": "alice", "content": "Budget review"},
    {"timestamp": "10:05", "speaker": "bob", "content": "Ship it"},
]


def _decoded(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _row_count(db) -> int:
    return (await db.execute(select(func.count()).select_from(MeetingSession))).scalar_one()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_meeting_session_table_shape():
    assert MeetingSession.__tablename__ == "meeting_sessions"

    columns = MeetingSession.__table__.c
    assert columns.user_id.nullable is False
    assert columns.user_id.index is True
    assert columns.session_id.unique is True
    assert columns.session_id.index is True
    # "mirror the length of the existing Conversation.session_id"
    assert columns.session_id.type.length == Conversation.__table__.c.session_id.type.length
    assert "participants" in columns
    assert "notes" in columns
    assert "status" in columns
    assert "created_at" in columns
    assert "updated_at" in columns


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_returns_none_when_absent(db):
    assert await meeting_session_get(db, "does-not-exist", 1) is None


@pytest.mark.asyncio
async def test_get_returns_none_for_other_user(db):
    await meeting_session_upsert(db, "s-own", 1, "Mine", PARTICIPANTS, NOTES)

    assert await meeting_session_get(db, "s-own", 1) is not None
    assert await meeting_session_get(db, "s-own", 2) is None


# ---------------------------------------------------------------------------
# upsert — create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_creates_row_with_active_default(db):
    row = await meeting_session_upsert(db, "s-1", 1, "Weekly sync", PARTICIPANTS, NOTES)

    assert row.session_id == "s-1"
    assert row.user_id == 1
    assert row.title == "Weekly sync"
    assert row.status == "active"
    assert _decoded(row.participants) == PARTICIPANTS
    assert _decoded(row.notes) == NOTES
    assert row.created_at is not None
    assert row.updated_at is not None
    assert await _row_count(db) == 1

    fetched = await meeting_session_get(db, "s-1", 1)
    assert fetched is not None
    assert fetched.session_id == "s-1"
    assert fetched.user_id == 1
    assert fetched.title == "Weekly sync"
    assert _decoded(fetched.participants) == PARTICIPANTS
    assert _decoded(fetched.notes) == NOTES


# ---------------------------------------------------------------------------
# upsert — update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_second_call_updates_without_duplicating(db):
    first = await meeting_session_upsert(db, "s-2", 1, "Original", ["alice"], NOTES)

    second = await meeting_session_upsert(
        db,
        "s-2",
        1,
        "Updated",
        ["carol"],
        [{"timestamp": "11:00", "speaker": "carol", "content": "Wrap up"}],
        status="ended",
    )

    assert second.id == first.id
    assert second.session_id == "s-2"
    assert second.user_id == 1
    assert second.title == "Updated"
    assert second.status == "ended"
    assert _decoded(second.participants) == ["carol"]
    assert _decoded(second.notes) == [{"timestamp": "11:00", "speaker": "carol", "content": "Wrap up"}]
    assert await _row_count(db) == 1

    fetched = await meeting_session_get(db, "s-2", 1)
    assert fetched is not None
    assert fetched.title == "Updated"
    assert fetched.status == "ended"
    assert _decoded(fetched.participants) == ["carol"]

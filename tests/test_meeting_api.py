"""RED tests for B3 — ``/meeting`` REST router.

Under test (to be created): ``crabagent.serve.api.meeting`` with ``APIRouter(prefix="/meeting")``
    GET  /meeting/{session_id}        -> state | 404
    PUT  /meeting/{session_id}        -> upsert (status "active") and return state
    POST /meeting/{session_id}/end    -> upsert status "ended", extract action items, return summary

``process_meeting_notes`` is faked so no LLM call happens; the DB is a throwaway
sqlite file wired through ``get_db``. Every route is scoped to the authenticated
user: a session created by user A is invisible (404) to user B.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db
from crabagent.core.meeting import processor
from crabagent.serve.api import meeting as meeting_api
from crabagent.serve.api.meeting import router
from crabagent.serve.deps import get_current_user

NOTES = [
    {"timestamp": "10:00", "speaker": "Alice", "content": "Budget review"},
    {"timestamp": "10:05", "speaker": "Bob", "content": "Ship it"},
]
FORMATTED_NOTES = "[10:00] Alice: Budget review\n[10:05] Bob: Ship it"

# Identity handed to ``get_current_user``; tests switch it to act as another user.
CURRENT_USER = {"id": 1}

EXTRACTION = {
    "summary": "Budget approved.",
    "action_items": [{"title": "Ship it", "assignee": "Bob"}],
    "created_tasks": [{"id": 5, "title": "Ship it", "assignee": "Bob", "priority": "medium"}],
}


@pytest.fixture
async def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'meeting_api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)

    async def database():
        async with factory() as session:
            yield session

    app = FastAPI()
    # The router is expected to carry its own "/meeting" prefix (repo convention),
    # so it is included without an extra prefix here.
    app.include_router(router)
    app.dependency_overrides[db.get_db] = database
    CURRENT_USER["id"] = 1
    app.dependency_overrides[get_current_user] = lambda: db.User(id=CURRENT_USER["id"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


@pytest.fixture
def fake_process(monkeypatch: pytest.MonkeyPatch):
    """Fake the extraction core; returns the list of recorded calls."""
    calls: list[dict] = []

    async def fake(notes="", meeting_title="", user_id=1, context=None, session_id="", **extra):
        calls.append(
            {
                "notes": notes,
                "meeting_title": meeting_title,
                "user_id": user_id,
                "session_id": session_id,
            }
        )
        return {k: v for k, v in EXTRACTION.items()}

    monkeypatch.setattr(processor, "process_meeting_notes", fake)
    if hasattr(meeting_api, "process_meeting_notes"):
        monkeypatch.setattr(meeting_api, "process_meeting_notes", fake, raising=False)
    return calls


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

async def test_get_unknown_session_returns_404(client):
    response = await client.get("/meeting/nope")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT then GET
# ---------------------------------------------------------------------------

async def test_put_creates_session_and_get_returns_it(client):
    response = await client.put(
        "/meeting/m-1",
        json={"title": "Weekly sync", "participants": ["Alice", "Bob"], "notes": NOTES},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "m-1"
    assert body["title"] == "Weekly sync"
    assert body["participants"] == ["Alice", "Bob"]
    assert body["notes"] == NOTES
    assert body["status"] == "active"
    assert "updated_at" in body

    fetched = await client.get("/meeting/m-1")
    assert fetched.status_code == 200
    state = fetched.json()
    assert state["session_id"] == "m-1"
    assert state["title"] == "Weekly sync"
    assert state["participants"] == ["Alice", "Bob"]
    assert state["notes"] == NOTES
    assert state["status"] == "active"


async def test_put_twice_updates_existing_session(client):
    await client.put("/meeting/m-2", json={"title": "First", "participants": [], "notes": []})
    await client.put(
        "/meeting/m-2",
        json={"title": "Second", "participants": ["Carol"], "notes": NOTES[:1]},
    )

    state = (await client.get("/meeting/m-2")).json()
    assert state["title"] == "Second"
    assert state["participants"] == ["Carol"]
    assert state["notes"] == NOTES[:1]
    assert state["status"] == "active"


# ---------------------------------------------------------------------------
# POST /end
# ---------------------------------------------------------------------------

async def test_end_meeting_extracts_tasks_and_marks_ended(client, fake_process):
    response = await client.post(
        "/meeting/m-3/end",
        json={"title": "Weekly sync", "participants": ["Alice"], "notes": NOTES},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "m-3"
    assert body["summary"] == "Budget approved."
    assert body["created_tasks"] == EXTRACTION["created_tasks"]
    assert body["status"] == "ended"

    assert len(fake_process) == 1, f"extraction must run exactly once, got {fake_process}"
    call = fake_process[0]
    assert call["session_id"] == "m-3"
    # notes are flattened to "[timestamp] speaker: content" lines joined by newlines
    assert call["notes"].strip() == FORMATTED_NOTES

    state = (await client.get("/meeting/m-3")).json()
    assert state["status"] == "ended"
    assert state["title"] == "Weekly sync"
    assert state["participants"] == ["Alice"]
    assert state["notes"] == NOTES


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

async def test_other_user_cannot_read_or_overwrite_session(client):
    await client.put("/meeting/x-1", json={"title": "Secret", "participants": ["Alice"], "notes": NOTES})

    CURRENT_USER["id"] = 2
    assert (await client.get("/meeting/x-1")).status_code == 404
    put = await client.put(
        "/meeting/x-1", json={"title": "Hijacked", "participants": [], "notes": []}
    )
    assert put.status_code == 404

    CURRENT_USER["id"] = 1
    state = (await client.get("/meeting/x-1")).json()
    assert state["title"] == "Secret"
    assert state["participants"] == ["Alice"]
    assert state["notes"] == NOTES
    assert state["status"] == "active"


async def test_other_user_cannot_end_session(client, fake_process):
    await client.put("/meeting/x-2", json={"title": "Secret", "participants": [], "notes": NOTES})

    CURRENT_USER["id"] = 2
    response = await client.post("/meeting/x-2/end", json={"title": "Hijacked", "participants": [], "notes": []})
    assert response.status_code == 404
    assert fake_process == []

    CURRENT_USER["id"] = 1
    state = (await client.get("/meeting/x-2")).json()
    assert state["title"] == "Secret"
    assert state["status"] == "active"

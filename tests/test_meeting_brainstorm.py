"""RED tests for the meeting BRAINSTORM feature.

Under test (to be created): ``crabagent.core.meeting.brainstorm``
    async def brainstorm(topic: str, participants: list[str], user_id: int = 1, context=None) -> dict
    -> {"ideas": [{"agent": str, "ideas": [str, ...]}],
        "reactions": [{"agent": str, "reactions": [
            {"idea_index": int, "verdict": str, "comment": str}]}]}

Endpoint (router already mounted at ``/meeting``):
    POST /meeting/{session_id}/brainstorm
    body {"topic": str, "participants": [str]}
    -> 200 {"session_id": str, "ideas": [...], "reactions": [...]}

Only ``litellm.acompletion`` is faked, so no network call happens. The agent
profiles live in a throwaway sqlite database wired in through
``crabagent.core.database.async_session_factory`` (patched both on the database
module and on the brainstorm module when it re-exports the factory, so the tests
do not depend on which import style the implementation picks).

The fake LLM answers with a payload containing BOTH an ``ideas`` and a
``reactions`` key, so it is valid for the idea pass and the reaction pass
regardless of how many calls the implementation makes per agent.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db
from crabagent.core import provider_store
from crabagent.serve.api import meeting as meeting_api
from crabagent.serve.api.meeting import router
from crabagent.serve.deps import get_current_user

pytestmark = pytest.mark.asyncio

TOPIC = "Q4 planning"

GENERIC_IDEAS = ["Idea alpha", "Idea beta"]
GENERIC_REACTIONS = [
    {"idea_index": 0, "verdict": "agree", "comment": "Solid."},
    {"idea_index": 1, "verdict": "disagree", "comment": "Too costly."},
]
GENERIC_PAYLOAD = json.dumps({"ideas": GENERIC_IDEAS, "reactions": GENERIC_REACTIONS})

# ``display_name`` mirrors ``name`` on purpose: the contract does not say whether
# the ``agent`` field carries the profile name or its display name, so both work.
PROFILES = [
    {
        "name": "alice",
        "display_name": "alice",
        "role": "Alice Role",
        "goal": "Alice Goal",
        "model": "fake-model",
    },
    {
        "name": "bob",
        "display_name": "bob",
        "role": "Bob Role",
        "goal": "Bob Goal",
        "model": "fake-model",
    },
    {
        "name": "carol",
        "display_name": "carol",
        "role": "Carol Role",
        "goal": "Carol Goal",
        "model": "fake-model",
    },
    {
        "name": "boom",
        "display_name": "boom",
        "role": "KABOOM Specialist",
        "goal": "detonate experiments",
        "model": "fake-model",
    },
    {
        "name": "garbled",
        "display_name": "garbled",
        "role": "Gibberish Responder",
        "goal": "emit broken json",
        "model": "fake-model",
    },
]

# Prompts are matched on the distinctive role/goal markers (never on the bare
# name) so a prompt that merely lists the other participants' names is not
# mistaken for that agent's own call.
RAISING_MARKERS = ("kaboom specialist", "detonate experiments")
GARBLED_MARKERS = ("gibberish responder", "emit broken json")


def brainstorm_module():
    """Import lazily: at HEAD the module does not exist yet (deliberate RED)."""
    return importlib.import_module("crabagent.core.meeting.brainstorm")


def _prompt_text(kwargs: dict) -> str:
    parts: list[str] = []
    for message in kwargs.get("messages") or []:
        if isinstance(message, dict):
            parts.append(str(message.get("content", "")))
        else:
            parts.append(str(getattr(message, "content", "")))
    for key, value in kwargs.items():
        if key != "messages" and isinstance(value, str):
            parts.append(value)
    return "\n".join(parts).lower()


class FakeLLM:
    """Stand-in for ``litellm.acompletion`` (records calls, returns JSON content)."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        prompt = _prompt_text(kwargs)
        if any(marker in prompt for marker in RAISING_MARKERS):
            raise RuntimeError("provider down")
        if any(marker in prompt for marker in GARBLED_MARKERS):
            content = "Sure! Here are my ideas: not json at all {{{"
        else:
            content = GENERIC_PAYLOAD
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
async def world(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Seeded profile DB + patched LLM/provider lookup + the module under test."""
    # Imported first so a missing module fails before any resources are created.
    module = brainstorm_module()

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'brainstorm.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        for profile in PROFILES:
            session.add(db.AgentProfile(user_id=1, enabled=True, **profile))
        await session.commit()

    # Patch with the sessionmaker itself: that is exactly what the real
    # ``database.async_session_factory`` attribute is (see database.py), so the
    # implementation's ``async with async_session_factory() as db`` is unchanged.
    monkeypatch.setattr(db, "async_session_factory", factory)

    llm = FakeLLM()
    monkeypatch.setattr("litellm.acompletion", llm)

    async def no_provider():
        return None

    # Keep model resolution offline/deterministic (no provider rows, no network).
    monkeypatch.setattr(provider_store, "get_default_provider", no_provider)

    if hasattr(module, "async_session_factory"):
        monkeypatch.setattr(module, "async_session_factory", factory, raising=False)
    if hasattr(module, "get_default_provider"):
        monkeypatch.setattr(module, "get_default_provider", no_provider, raising=False)
    if hasattr(module, "acompletion"):
        monkeypatch.setattr(module, "acompletion", llm, raising=False)
    if hasattr(module, "litellm"):
        monkeypatch.setattr(module.litellm, "acompletion", llm, raising=False)

    yield SimpleNamespace(
        brainstorm=module.brainstorm,
        module=module,
        llm=llm,
        factory=factory,
    )

    await engine.dispose()


# ---------------------------------------------------------------------------
# R1 — result shape
# ---------------------------------------------------------------------------

async def test_r1_returns_ideas_and_reactions_lists(world):
    result = await world.brainstorm(TOPIC, ["alice"])

    assert isinstance(result, dict)
    assert set(result) == {"ideas", "reactions"}, f"unexpected keys: {sorted(result)}"
    assert isinstance(result["ideas"], list)
    assert isinstance(result["reactions"], list)

    # Same shape rule, one level down: one participant -> one entry per pass.
    assert len(result["ideas"]) == 1, f"expected one ideas entry, got {result['ideas']}"
    assert len(result["reactions"]) == 1, f"expected one reactions entry, got {result['reactions']}"
    assert set(result["ideas"][0]) == {"agent", "ideas"}
    assert isinstance(result["ideas"][0]["agent"], str)
    assert set(result["reactions"][0]) == {"agent", "reactions"}
    assert isinstance(result["reactions"][0]["agent"], str)
    for item in result["reactions"][0]["reactions"]:
        assert set(item) == {"idea_index", "verdict", "comment"}, f"unexpected item keys: {sorted(item)}"


# ---------------------------------------------------------------------------
# R2 — no participants means no LLM work
# ---------------------------------------------------------------------------

async def test_r2_empty_participants_short_circuits_without_llm(world):
    result = await world.brainstorm(TOPIC, [])

    assert result == {"ideas": [], "reactions": []}
    assert world.llm.calls == [], (
        f"no LLM call is allowed with zero participants, got {len(world.llm.calls)}"
    )


# ---------------------------------------------------------------------------
# R3 — idea pass: one entry per participant, in participant order
# ---------------------------------------------------------------------------

async def test_r3_idea_pass_one_entry_per_participant_in_order(world):
    result = await world.brainstorm(TOPIC, ["bob", "alice"])

    ideas = result["ideas"]
    assert len(ideas) == 2, f"expected one ideas entry per participant, got {ideas}"
    assert [entry["agent"] for entry in ideas] == ["bob", "alice"], (
        f"entries must follow participant order, got {[e.get('agent') for e in ideas]}"
    )
    for entry in ideas:
        assert isinstance(entry["ideas"], list)
        assert entry["ideas"] == GENERIC_IDEAS, (
            f"ideas must come from the LLM JSON, got {entry['ideas']!r}"
        )
        assert all(isinstance(idea, str) for idea in entry["ideas"])


# ---------------------------------------------------------------------------
# R4 — reaction pass: well-formed reaction items
# ---------------------------------------------------------------------------

async def test_r4_reaction_pass_items_are_well_formed(world):
    result = await world.brainstorm(TOPIC, ["bob", "alice"])

    reactions = result["reactions"]
    assert len(reactions) == 2, f"expected one reactions entry per participant, got {reactions}"
    assert [entry["agent"] for entry in reactions] == ["bob", "alice"]
    for entry in reactions:
        assert isinstance(entry["reactions"], list)
        assert entry["reactions"], f"expected reactions from {entry['agent']}"
        for item in entry["reactions"]:
            assert isinstance(item["idea_index"], int) and not isinstance(item["idea_index"], bool), (
                f"idea_index must be an int, got {item['idea_index']!r}"
            )
            assert item["verdict"] in {"agree", "disagree", "ignore"}, (
                f"unexpected verdict {item['verdict']!r}"
            )
            assert isinstance(item["comment"], str)
            assert item["comment"].strip(), "comment must not be blank"
        assert [item["idea_index"] for item in entry["reactions"]] == [0, 1]
        assert [item["verdict"] for item in entry["reactions"]] == ["agree", "disagree"]


# ---------------------------------------------------------------------------
# R5 — a failing agent is skipped, the others still appear
# ---------------------------------------------------------------------------

async def test_r5_failing_agent_is_skipped(world):
    result = await world.brainstorm(TOPIC, ["alice", "boom", "bob"])

    assert [entry["agent"] for entry in result["ideas"]] == ["alice", "bob"], (
        "the agent whose LLM call raises must be skipped, the rest must survive"
    )
    assert [entry["agent"] for entry in result["reactions"]] == ["alice", "bob"]
    for entry in result["ideas"]:
        assert entry["ideas"] == GENERIC_IDEAS


# ---------------------------------------------------------------------------
# R6 — malformed JSON means empty entries, not a crash
# ---------------------------------------------------------------------------

async def test_r6_malformed_json_contributes_empty_entries(world):
    result = await world.brainstorm(TOPIC, ["alice", "garbled"])

    assert [entry["agent"] for entry in result["ideas"]] == ["alice", "garbled"]
    assert [entry["agent"] for entry in result["reactions"]] == ["alice", "garbled"]

    assert result["ideas"][0]["ideas"] == GENERIC_IDEAS
    assert result["ideas"][1]["ideas"] == [], (
        f"unparseable JSON must yield no ideas, got {result['ideas'][1]['ideas']!r}"
    )
    assert result["reactions"][1]["reactions"] == [], (
        f"unparseable JSON must yield no reactions, got {result['reactions'][1]['reactions']!r}"
    )


# ---------------------------------------------------------------------------
# B — POST /meeting/{session_id}/brainstorm
# ---------------------------------------------------------------------------

API_IDEAS = [{"agent": "alice", "ideas": ["Ship it"]}]
API_REACTIONS = [
    {"agent": "alice", "reactions": [{"idea_index": 0, "verdict": "agree", "comment": "Yes."}]}
]


@pytest.fixture
async def api_client(world):
    async def database():
        async with world.factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[db.get_db] = database
    app.dependency_overrides[get_current_user] = lambda: db.User(id=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_endpoint_returns_session_id_ideas_and_reactions(world, api_client, monkeypatch):
    recorded: list[dict] = []

    async def fake_brainstorm(topic, participants, user_id=1, context=None, **extra):
        recorded.append(
            {"topic": topic, "participants": list(participants), "user_id": user_id}
        )
        return {"ideas": API_IDEAS, "reactions": API_REACTIONS}

    monkeypatch.setattr(world.module, "brainstorm", fake_brainstorm)
    if hasattr(meeting_api, "brainstorm"):
        monkeypatch.setattr(meeting_api, "brainstorm", fake_brainstorm, raising=False)

    response = await api_client.post(
        "/meeting/s-1/brainstorm",
        json={"topic": "Launch plan", "participants": ["alice", "bob"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == "s-1"
    assert body["ideas"] == API_IDEAS
    assert body["reactions"] == API_REACTIONS

    assert len(recorded) == 1, f"brainstorm must run exactly once, got {recorded}"
    assert recorded[0]["topic"] == "Launch plan"
    assert recorded[0]["participants"] == ["alice", "bob"]


async def test_endpoint_passes_session_model_via_context(world, api_client, monkeypatch):
    """Regression: the endpoint must forward the session's model, not a hardcoded one."""
    captured: list[dict] = []

    async def fake_brainstorm(topic, participants, user_id=1, context=None, **extra):
        captured.append({"context": context})
        return {"ideas": API_IDEAS, "reactions": API_REACTIONS}

    async def fake_get_conversation(db_session, session_id):
        return SimpleNamespace(model="deepseek-flash")

    monkeypatch.setattr(world.module, "brainstorm", fake_brainstorm)
    if hasattr(meeting_api, "brainstorm"):
        monkeypatch.setattr(meeting_api, "brainstorm", fake_brainstorm, raising=False)
    monkeypatch.setattr(
        "crabagent.serve.services.conversation.get_conversation", fake_get_conversation
    )

    response = await api_client.post(
        "/meeting/s-model/brainstorm",
        json={"topic": "Launch plan", "participants": ["alice"]},
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1, f"brainstorm must run exactly once, got {captured}"
    context = captured[0]["context"]
    assert context is not None, "endpoint must pass a context carrying the session model"
    assert context.metadata["model"] == "deepseek-flash"

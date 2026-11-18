"""Contract tests for the per-agent token-usage API.

Pins four behaviours of ``/api/token-usage`` that the frontend relies on:

1. ``by_agent`` items carry ``non_cached_tokens``, ``reasoning_tokens`` and
   ``cache_hit_rate`` (cached/prompt rounded to 4 dp, 0 when prompt is 0).
2. ``sum(by_agent[].total_tokens) == overview["total_tokens"]`` for the same
   days/workspace filters.
3. ``GET /token-usage/sessions?agent=<name>`` returns only the sessions that
   have rows for that exact agent, and ``total`` counts only those sessions.
4. An ``agent`` name with no rows yields ``{"sessions": [], "total": 0}``.

The seeded rows are written straight into the ``token_usage`` table so the
assertions exercise the real aggregation SQL (no mocked query results).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db
from crabagent.serve.api.token_usage import router
from crabagent.serve.deps import get_current_user

USER_ID = 1
WS_ALPHA = "/tmp/ws-alpha"
WS_BETA = "/tmp/ws-beta"

# (session_id, agent_name, model, prompt, cached, completion, reasoning)
_SEED_ROWS = [
    ("sess-a", "coder", "gpt-x", 100, 40, 50, 10),
    ("sess-a", "coder", "gpt-y", 200, 0, 100, 0),
    ("sess-b", "default", "gpt-x", 50, 0, 50, 0),
    # Zero-prompt edge case: cache_hit_rate must not divide by zero.
    ("sess-b", "helper", "gpt-y", 0, 0, 20, 0),
]


def _usage_row(session_id, agent, model, prompt, cached, completion, reasoning) -> db.TokenUsage:
    return db.TokenUsage(
        user_id=USER_ID,
        session_id=session_id,
        agent_name=agent,
        model=model,
        provider="test",
        prompt_tokens=prompt,
        cached_tokens=cached,
        non_cached_tokens=prompt - cached,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        total_tokens=prompt + completion,
        iteration=1,
        branch_id="main",
    )


@pytest.fixture
async def client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)

    async with factory() as session:
        session.add_all(
            [
                db.Conversation(
                    session_id="sess-a", user_id=USER_ID, title="Alpha chat",
                    workspace=WS_ALPHA, active_branch="main",
                ),
                db.Conversation(
                    session_id="sess-b", user_id=USER_ID, title="Beta chat",
                    workspace=WS_BETA, active_branch="main",
                ),
            ]
        )
        session.add_all([_usage_row(*row) for row in _SEED_ROWS])
        await session.commit()

    async def _database():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[db.get_db] = _database
    app.dependency_overrides[get_current_user] = lambda: db.User(id=USER_ID)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def _overview(client, **params) -> dict:
    response = await client.get("/api/token-usage/overview", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def _sessions(client, **params):
    response = await client.get("/api/token-usage/sessions", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _by_agent(overview: dict) -> dict[str, dict]:
    return {item["agent_name"]: item for item in overview["by_agent"]}


# ── rule 1: by_agent item shape ───────────────────────────────────────


async def test_by_agent_item_exposes_new_token_fields(client):
    coder = _by_agent(await _overview(client))["coder"]

    assert {
        "agent_name",
        "total_tokens",
        "prompt_tokens",
        "cached_tokens",
        "non_cached_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cache_hit_rate",
        "calls",
    } <= set(coder)

    assert coder["non_cached_tokens"] == 260  # (100-40) + (200-0)
    assert coder["reasoning_tokens"] == 10
    assert coder["cache_hit_rate"] == round(40 / 300, 4)


async def test_by_agent_cache_hit_rate_zero_when_prompt_is_zero(client):
    helper = _by_agent(await _overview(client))["helper"]

    assert helper["prompt_tokens"] == 0
    assert helper["cache_hit_rate"] == 0


# ── rule 2: totals invariant ──────────────────────────────────────────


@pytest.mark.parametrize("workspace", ["", WS_ALPHA, WS_BETA])
async def test_by_agent_totals_sum_to_overview_total(client, workspace):
    overview = await _overview(client, days=30, workspace=workspace)

    agent_total = sum(item["total_tokens"] for item in overview["by_agent"])
    assert agent_total == overview["total_tokens"]

    # The filter really narrowed the data (guard against a vacuous 0 == 0 pass).
    if workspace:
        assert overview["total_tokens"] > 0
        assert set(_by_agent(overview)) == ({"coder"} if workspace == WS_ALPHA else {"default", "helper"})
    else:
        assert overview["total_tokens"] == 570


# ── rule 3: sessions agent filter ─────────────────────────────────────


async def test_sessions_filtered_by_agent_counts_only_matching_sessions(client):
    coder = await _sessions(client, agent="coder")
    assert [s["session_id"] for s in coder["sessions"]] == ["sess-a"]
    assert coder["total"] == 1  # not the unfiltered 2

    default = await _sessions(client, agent="default")
    assert [s["session_id"] for s in default["sessions"]] == ["sess-b"]
    assert default["total"] == 1


async def test_sessions_without_agent_returns_all_sessions(client):
    unfiltered = await _sessions(client)
    assert {s["session_id"] for s in unfiltered["sessions"]} == {"sess-a", "sess-b"}
    assert unfiltered["total"] == 2


# ── rule 4: unknown agent is empty, not an error ──────────────────────


async def test_sessions_unknown_agent_returns_empty(client):
    payload = await _sessions(client, agent="agent-with-no-rows")

    assert payload == {"sessions": [], "total": 0}

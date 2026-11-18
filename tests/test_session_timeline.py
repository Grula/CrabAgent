"""The chat timeline: every agent's steps in one chronological list."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db
from crabagent.serve.services import timeline


def _call(name, started, args=None, result="ok", ok=True):
    return {"name": name, "args": {"summary": f"do {name}", **(args or {})}, "started_at": started,
            "elapsed": 0.5, "result_summary": result, "ok": ok}


@pytest.fixture
def runs(monkeypatch):
    rows = [
        {"id": 1, "parent_run_id": None, "agent_name": "main", "status": "completed", "task_summary": "build",
         "started_at": 0.0, "finished_at": 50.0, "elapsed": 50.0, "error": "",
         "tool_calls": [_call("read", 10.0, {"file_path": "SPEC.md"}), _call("run_pipeline", 30.0)]},
        {"id": 2, "parent_run_id": None, "agent_name": "pipeline", "status": "completed", "task_summary": "",
         "started_at": 31.0, "finished_at": 49.0, "elapsed": 18.0, "error": "", "tool_calls": []},
        {"id": 3, "parent_run_id": 2, "agent_name": "tester", "status": "running", "task_summary": "tests",
         "started_at": 32.0, "finished_at": None, "elapsed": 0.0, "error": "",
         "tool_calls": [_call("bash", 20.0, {"command": "pytest -q"}, ok=False),
                        _call("write", 40.0, {"file_path": "t.py", "content": "x" * (timeline.MAX_ARG_CHARS + 50)}),
                        _call("bash", 45.0, {"command": "pytest -q"}, result=None)]},
    ]

    async def fake_runs(session_id, user_id, limit=300):
        return rows

    async def fake_registry():
        return [{"name": "architect", "display_name": "System Architect", "icon": "🧠"},
                {"name": "tester", "display_name": "QA Engineer", "icon": "🧪"}]

    monkeypatch.setattr(timeline, "agent_runs_for_session", fake_runs)
    monkeypatch.setattr("crabagent.core.agent.agents.load_agent_registry", fake_registry)


@pytest.mark.asyncio
async def test_steps_from_all_agents_come_back_in_time_order(runs):
    result = await timeline.build_timeline("s1", 1, chat_agent="architect")

    assert [(s["agent"], s["tool"]) for s in result["steps"]] == [
        ("architect", "read"), ("tester", "bash"), ("architect", "run_pipeline"), ("tester", "write"), ("tester", "bash")]
    assert [r["agent"] for r in result["runs"]] == ["architect", "tester"]  # the pipeline container is left out
    assert result["runs"][0]["display_name"] == "System Architect"
    assert result["running"] is True


@pytest.mark.asyncio
async def test_each_step_carries_its_status_command_and_trimmed_arguments(runs):
    steps = (await timeline.build_timeline("s1", 1, chat_agent="architect"))["steps"]
    failed_bash, write, running_bash = steps[1], steps[3], steps[4]

    assert failed_bash["status"] == "failed" and failed_bash["args"] == {"command": "pytest -q"}
    assert failed_bash["summary"] == "do bash"  # the agent's own label, kept out of args
    assert running_bash["status"] == "running"
    assert len(write["args"]["content"]) < timeline.MAX_ARG_CHARS + 50
    assert write["args"]["content"].endswith("(truncated)")


@pytest.mark.asyncio
async def test_runs_query_returns_only_this_chat_and_user_oldest_first(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)
    async with factory() as session:
        for session_id, user_id, agent in (("s1", 1, "a"), ("s2", 1, "b"), ("s1", 2, "c"), ("s1", 1, "d")):
            session.add(db.AgentRun(user_id=user_id, session_id=session_id, agent_name=agent,
                                    tool_calls=[{"name": "bash", "args": {"command": "ls"}}]))
        await session.commit()

    rows = await db.agent_runs_for_session("s1", 1)
    await engine.dispose()

    assert [r["agent_name"] for r in rows] == ["a", "d"]
    assert rows[0]["tool_calls"][0]["args"]["command"] == "ls"


@pytest.mark.asyncio
async def test_timeline_endpoint_serves_the_owner_and_refuses_others(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from crabagent.serve.api.session import router
    from crabagent.serve.deps import get_current_user

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)
    async with factory() as session:
        session.add_all([db.Conversation(session_id="mine", user_id=1, title="m", agent="architect", active_branch="main"),
                         db.Conversation(session_id="theirs", user_id=2, title="t", active_branch="main"),
                         db.AgentRun(user_id=1, session_id="mine", agent_name="main",
                                     tool_calls=[{"name": "bash", "args": {"command": "ls"}, "started_at": 1.0,
                                                  "result_summary": "README.md", "ok": True}])])
        await session.commit()

    async def database():
        async with factory() as session:
            yield session

    async def no_profiles():
        return []

    monkeypatch.setattr("crabagent.core.agent.agents.load_agent_registry", no_profiles)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[db.get_db] = database
    app.dependency_overrides[get_current_user] = lambda: db.User(id=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mine = await client.get("/api/sessions/mine/timeline")
        theirs = await client.get("/api/sessions/theirs/timeline")
    await engine.dispose()

    assert mine.status_code == 200
    body = mine.json()
    assert body["steps"][0]["agent"] == "architect" and body["steps"][0]["args"] == {"command": "ls"}
    assert theirs.status_code == 403

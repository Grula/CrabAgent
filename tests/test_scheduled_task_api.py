"""Regression tests for cron validation on the scheduled-task HTTP API.

A cron expression must be rejected with HTTP 400 when its field *values* are out
of range (e.g. minute 99, hour 24), not only when the field count is wrong.
Today ``create_task``/``update_task`` persist such an expression, APScheduler
silently refuses to schedule it and the task never runs.

Harness: the real router mounted on a throwaway FastAPI app, a throwaway SQLite
database via a ``get_db`` dependency override, a fake user via
``get_current_user``, and ``get_scheduler`` monkeypatched to an in-memory no-op
so no real APScheduler (or real database) is ever touched.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db
from crabagent.serve.api import scheduled_task as scheduled_task_api
from crabagent.serve.deps import get_current_user

VALID_CRON = "0 9 * * 1-5"


class _FakeScheduler:
    """Stands in for SchedulerService: records calls, never schedules anything."""

    def __init__(self):
        self.added: list[int] = []
        self.removed: list[int] = []

    async def add_task(self, task):
        self.added.append(task.id)

    def remove_task(self, task_id):
        self.removed.append(task_id)


@pytest.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def database():
        async with factory() as session:
            yield session

    fake_scheduler = _FakeScheduler()
    monkeypatch.setattr(scheduled_task_api, "get_scheduler", lambda: fake_scheduler)

    app = FastAPI()
    app.include_router(scheduled_task_api.router)
    app.dependency_overrides[db.get_db] = database
    app.dependency_overrides[get_current_user] = lambda: db.User(id=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, fake_scheduler
    await engine.dispose()


async def _create(client, cron: str):
    return await client.post(
        "/scheduled-tasks",
        json={"name": "daily report", "prompt": "summarise yesterday", "cron_expression": cron},
    )


async def _make_task(client) -> int:
    response = await _create(client, VALID_CRON)
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def test_create_rejects_out_of_range_minute(env):
    client, _, _ = env
    response = await _create(client, "99 * * * *")
    assert response.status_code == 400, response.text
    assert "cron" in response.json()["detail"].lower()


async def test_update_rejects_out_of_range_hour(env):
    client, _, _ = env
    task_id = await _make_task(client)
    response = await client.patch(f"/scheduled-tasks/{task_id}", json={"cron_expression": "0 24 * * *"})
    assert response.status_code == 400, response.text


async def test_update_rejects_out_of_range_minute(env):
    client, _, _ = env
    task_id = await _make_task(client)
    response = await client.patch(f"/scheduled-tasks/{task_id}", json={"cron_expression": "99 99 * * *"})
    assert response.status_code == 400, response.text


async def test_create_accepts_valid_cron(env):
    client, _, scheduler = env
    response = await _create(client, VALID_CRON)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cron_expression"] == VALID_CRON
    assert body["enabled"] is True
    assert scheduler.added == [body["id"]]


async def test_create_rejects_wrong_field_count(env):
    client, _, _ = env
    response = await _create(client, "0 9 * *")
    assert response.status_code == 400, response.text


async def test_update_without_cron_expression_is_not_rejected(env):
    client, _, _ = env
    task_id = await _make_task(client)
    response = await client.patch(f"/scheduled-tasks/{task_id}", json={"name": "renamed"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "renamed"
    assert response.json()["cron_expression"] == VALID_CRON

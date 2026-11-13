"""Tests for trusted work system Phase 1: Task ↔ Run service + startup recovery."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core.database import Base
from crabagent.core.task import service as task_service
from crabagent.core.task import store as task_store
from crabagent.core.task.recovery import recover_interrupted_state


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_add_task_classifies_owner(db):
    t1 = await task_store.add_task(db, user_id=1, title="agent work", assignee="crabagent")
    assert t1["owner_type"] == "agent"
    assert t1["owner_name"] == "CrabAgent"

    t2 = await task_store.add_task(db, user_id=1, title="human work", assignee="xcl")
    assert t2["owner_type"] == "human"
    assert t2["owner_name"] == "xcl"

    t3 = await task_store.add_task(db, user_id=1, title="no assignee")
    assert t3["owner_type"] == "human"
    assert t3["verification_status"] == "unverified"


@pytest.mark.asyncio
async def test_start_agent_run_links_run_and_task(db):
    task = await task_store.add_task(db, user_id=1, title="Generate report")

    updated, run_id = await task_service.start_agent_run(
        db, task["id"], 1, agent_name="main", session_id="sess-1", phase="reading data"
    )

    assert updated["status"] == "in_progress"
    assert updated["active_run_id"] == run_id
    assert updated["last_run_id"] == run_id
    assert updated["started_at"] is not None

    runs = await task_service.list_task_runs(db, task["id"], 1)
    assert len(runs) == 1
    assert runs[0]["task_id"] == task["id"]
    assert runs[0]["status"] == "running"
    assert runs[0]["phase"] == "reading data"


@pytest.mark.asyncio
async def test_start_agent_run_missing_task_raises(db):
    with pytest.raises(LookupError):
        await task_service.start_agent_run(db, 999, 1)


@pytest.mark.asyncio
async def test_finish_run_completed_marks_task_done(db):
    task = await task_store.add_task(db, user_id=1, title="Generate report")
    _, run_id = await task_service.start_agent_run(db, task["id"], 1)

    updated = await task_service.finish_agent_run(
        db, run_id, 1, "completed", result_summary="Report generated", tokens_used=120
    )

    assert updated["status"] == "done"
    assert updated["active_run_id"] is None
    assert updated["completed_at"] is not None
    assert updated["result_summary"] == "Report generated"


@pytest.mark.asyncio
async def test_finish_run_failed_marks_task_failed(db):
    task = await task_store.add_task(db, user_id=1, title="Generate report")
    _, run_id = await task_service.start_agent_run(db, task["id"], 1)

    updated = await task_service.finish_agent_run(db, run_id, 1, "failed", error="API timeout")

    assert updated["status"] == "failed"
    assert "API timeout" in updated["warning_summary"]


@pytest.mark.asyncio
async def test_finish_run_cancelled_after_done_keeps_done(db):
    task = await task_store.add_task(db, user_id=1, title="Generate report")
    _, run_id = await task_service.start_agent_run(db, task["id"], 1)
    # A summary-only task completes as done under trusted judgment.
    await task_service.finish_agent_run(db, run_id, 1, "completed", result_summary="Report completed")

    # A late cancellation arriving after done must not downgrade the task.
    updated = await task_service.finish_agent_run(db, run_id, 1, "cancelled")

    assert updated is not None
    assert updated["status"] == "done"


@pytest.mark.asyncio
async def test_cancel_task_finalizes_active_run(db):
    task = await task_store.add_task(db, user_id=1, title="Generate report")
    _, run_id = await task_service.start_agent_run(db, task["id"], 1)

    updated = await task_service.cancel_task(db, task["id"], 1, reason="User stopped")

    assert updated["status"] == "cancelled"
    runs = await task_service.list_task_runs(db, task["id"], 1)
    assert runs[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_mark_result_viewed(db):
    task = await task_store.add_task(db, user_id=1, title="Generate report")

    updated = await task_service.mark_result_viewed(db, task["id"], 1)

    assert updated["result_viewed_at"] is not None


@pytest.mark.asyncio
async def test_recovery_interrupts_zombie_runs_and_tasks(db):
    task = await task_store.add_task(db, user_id=1, title="Generate report")
    _, run_id = await task_service.start_agent_run(db, task["id"], 1)

    summary = await recover_interrupted_state(db)

    assert summary["interrupted_runs"] == 1
    runs = await task_service.list_task_runs(db, task["id"], 1)
    assert runs[0]["status"] == "interrupted"
    assert runs[0]["interrupted_reason"]

    task_after = await task_store.get_task(db, task["id"], 1)
    assert task_after["status"] == "failed"  # no result summary → failed
    assert task_after["active_run_id"] is None


@pytest.mark.asyncio
async def test_recovery_with_partial_result_marks_partial(db):
    task = await task_store.add_task(db, user_id=1, title="Generate report")
    await task_service.start_agent_run(db, task["id"], 1)
    await task_store.update_task(db, task["id"], 1, result_summary="Excel summary generated, Word incomplete")

    summary = await recover_interrupted_state(db)

    assert summary["partial_tasks"] == 1
    task_after = await task_store.get_task(db, task["id"], 1)
    assert task_after["status"] == "partial"


@pytest.mark.asyncio
async def test_finish_run_broadcasts_task_updated_with_session(db, monkeypatch):
    """The result-card chain: finish must broadcast task_updated carrying
    the session_id so the creating conversation receives a card."""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "crabagent.core.task.service.broadcast_task_event",
        lambda t, d: events.append((t, d)),
    )

    task = await task_store.add_task(db, user_id=1, title="Generate report")
    _, run_id = await task_service.start_agent_run(db, task["id"], 1, session_id="sess-1")

    # start broadcast
    assert any(t == "task_updated" and d["status"] == "in_progress" for t, d in events)

    await task_service.finish_agent_run(db, run_id, 1, "completed", result_summary="Report completed")

    # terminal broadcast with card payload
    updates = [d for t, d in events if t == "task_updated"]
    final = updates[-1]
    assert final["task_id"] == task["id"]
    assert final["status"] == "done"
    assert final["session_id"] == "sess-1"
    assert final["title"] == "Generate report"
    assert final["result_summary"] == "Report completed"


@pytest.mark.asyncio
async def test_recovery_is_idempotent(db):
    task = await task_store.add_task(db, user_id=1, title="Generate report")
    await task_service.start_agent_run(db, task["id"], 1)

    first = await recover_interrupted_state(db)
    second = await recover_interrupted_state(db)

    assert any(first.values())
    assert second == {
        "interrupted_runs": 0,
        "partial_tasks": 0,
        "failed_tasks": 0,
        "expired_requests": 0,
    }


@pytest.mark.asyncio
async def test_recompletion_rearms_unread_result(db):
    """The second done must re-arm a "new result": result_viewed_at resets to unread."""
    import datetime

    from sqlalchemy import select

    from crabagent.core.database import Task

    task = await task_store.add_task(db, user_id=1, title="Generate report")
    tid = task["id"]

    # First completion → the user views the result (read)
    await task_store.update_task(db, tid, 1, status="done", result_summary="V1")
    row = (await db.execute(select(Task).where(Task.id == tid))).scalar_one()
    row.result_viewed_at = datetime.datetime.now()
    await db.commit()
    viewed = await task_store.get_task(db, tid, 1)
    assert viewed["result_viewed_at"] is not None

    # Non-terminal updates must not disturb the read state
    await task_store.update_task(db, tid, 1, priority="high")
    still_viewed = await task_store.get_task(db, tid, 1)
    assert still_viewed["result_viewed_at"] is not None

    # Second completion (rework) → the result becomes unread again
    await task_store.update_task(db, tid, 1, status="done", result_summary="V2 deep dive")
    refreshed = await task_store.get_task(db, tid, 1)
    assert refreshed["result_viewed_at"] is None
    assert refreshed["result_summary"] == "V2 deep dive"


@pytest.mark.asyncio
async def test_judge_task_recompletion_rearms_unread_result(db):
    """completion.judge_task also resets the read state for a task completed again."""
    import datetime

    from sqlalchemy import select

    from crabagent.core.database import Task
    from crabagent.core.task.completion import judge_task

    task = await task_store.add_task(db, user_id=1, title="Generate report")
    tid = task["id"]

    # First judgement is done; the user has read it
    verdict1 = await judge_task(db, tid, 1)
    assert verdict1["status"] == "failed"  # No artifact, no summary → failed; seed the status first
    row = (await db.execute(select(Task).where(Task.id == tid))).scalar_one()
    row.status = "done"
    row.result_summary = "V1"
    row.result_viewed_at = datetime.datetime.now()
    await db.commit()

    # Second round: re-judge after result_summary is written (the reworked deep dive)
    row.result_summary = "V2 deep dive"
    await db.commit()
    verdict2 = await judge_task(db, tid, 1)
    assert verdict2["status"] == "done"

    refreshed = (await db.execute(select(Task).where(Task.id == tid))).scalar_one()
    assert refreshed.result_viewed_at is None

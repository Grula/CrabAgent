import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from crabagent.core.agent.agents import BackgroundAgentResult
from crabagent.core.agent.tools.agent import run_pipeline


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr('crabagent.core.database.run_record_create', AsyncMock(return_value=1))
    return SimpleNamespace(metadata={}, event_bus=SimpleNamespace(emit=AsyncMock()))


def step(id, deps=()):
    return {'id': id, 'agent_name': 'agent', 'task': id, 'depends_on': list(deps)}


@pytest.mark.asyncio
@pytest.mark.parametrize('steps, error', [
    ([step('a'), step('a')], 'duplicate'),
    ([step('a', ['missing'])], 'unknown'),
    ([step('a', ['a'])], 'cyclic'),
    ([step('a', ['b']), step('b', ['a'])], 'cyclic'),
])
async def test_invalid_graph_has_no_side_effects(context, monkeypatch, steps, error):
    spawn = AsyncMock()
    monkeypatch.setattr('crabagent.core.agent.agents.spawn_sub_agent', spawn)
    assert error in await run_pipeline(steps, context)
    spawn.assert_not_called()
    context.event_bus.emit.assert_not_called()


@pytest.mark.asyncio
async def test_dynamic_scheduling_and_failure_propagation(context, monkeypatch):
    slow_started = asyncio.Event()
    child_started = asyncio.Event()
    async def spawn(agent, task, ctx, **kwargs):
        sid = kwargs['pipeline_step_id']
        if sid == 'slow':
            slow_started.set()
            await asyncio.wait_for(child_started.wait(), 1)
        if sid == 'fast':
            await slow_started.wait()
            return BackgroundAgentResult('succeeded', 'Error: valid answer')
        if sid == 'child':
            assert 'Error: valid answer' in task
            child_started.set()
        if sid == 'bad':
            return BackgroundAgentResult('failed', 'failure without error prefix')
        assert sid not in ('blocked', 'transitive')
        return BackgroundAgentResult('succeeded', 'ok')
    monkeypatch.setattr('crabagent.core.agent.agents.spawn_sub_agent', spawn)
    result = await run_pipeline([step('slow'), step('fast'), step('child', ['fast']),
                                 step('bad'), step('blocked', ['bad']),
                                 step('transitive', ['blocked'])], context)
    assert '(3/6 succeeded)' in result
    assert 'Step: child [OK]' in result
    assert 'Step: transitive [FAIL]' in result


@pytest.mark.asyncio
async def test_cancellation_cleans_up_children(context, monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    async def spawn(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr('crabagent.core.agent.agents.spawn_sub_agent', spawn)
    task = asyncio.create_task(run_pipeline([step('a')], context))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_pipeline_passes_same_workspace_to_all_steps(tmp_path, monkeypatch):
    """Every pipeline step must receive the parent context (same workspace)."""
    parent_ws = tmp_path / "project"
    parent_ws.mkdir()
    ctx = SimpleNamespace(
        workspace=parent_ws,
        metadata={},
        event_bus=SimpleNamespace(emit=AsyncMock()),
    )
    monkeypatch.setattr('crabagent.core.database.run_record_create', AsyncMock(return_value=1))

    seen = []

    async def spawn(agent, task, parent_context, **kwargs):
        seen.append(parent_context.workspace)
        return BackgroundAgentResult('succeeded', 'ok')

    monkeypatch.setattr('crabagent.core.agent.agents.spawn_sub_agent', spawn)

    await run_pipeline([step('s1'), step('s2', ['s1']), step('s3')], ctx)

    assert len(seen) == 3
    assert all(ws == parent_ws for ws in seen)


# ── ordering must be explicit ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_step_without_depends_on_is_rejected_before_anything_starts(context, monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr('crabagent.core.agent.agents.spawn_sub_agent', spawn)
    steps = [step('tests'), {'id': 'code', 'agent_name': 'agent', 'task': 'code'}]

    result = await run_pipeline(steps, context)

    assert 'must set depends_on' in result and 'code' in result
    spawn.assert_not_called()
    context.event_bus.emit.assert_not_called()


@pytest.mark.asyncio
async def test_a_pipeline_where_nothing_depends_on_anything_is_rejected(context, monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr('crabagent.core.agent.agents.spawn_sub_agent', spawn)

    result = await run_pipeline([step('tests'), step('code'), step('review')], context)

    assert 'delegate_parallel' in result and 'Nothing was started' in result
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_delegate_parallel_still_runs_independent_tasks(context, monkeypatch):
    from crabagent.core.agent.tools.agent import delegate_parallel

    spawn = AsyncMock(return_value=BackgroundAgentResult('succeeded', 'ok'))
    monkeypatch.setattr('crabagent.core.agent.agents.spawn_sub_agent', spawn)

    result = await delegate_parallel([{'agent_name': 'a', 'task': 'x'}, {'agent_name': 'b', 'task': 'y'}], context)

    assert '(2/2 succeeded)' in result
    assert spawn.await_count == 2

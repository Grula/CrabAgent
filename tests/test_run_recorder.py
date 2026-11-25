"""Tests for RunRecorder event handling."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from crabagent.core.agent.run_recorder import RunRecorder
from crabagent.core.event import AgentEvent, EventType


@pytest.fixture
def recorder():
    return RunRecorder(user_id=1, session_id="s1", model="gpt-4o")


@pytest.mark.asyncio
async def test_on_event_handles_agent_start(recorder, monkeypatch: pytest.MonkeyPatch):
    called = {}

    async def fake_create(**kwargs):
        called.update(kwargs)
        return 42

    monkeypatch.setattr("crabagent.core.database.run_record_create", fake_create)

    await recorder.on_event(AgentEvent(type=EventType.AGENT_START, data={"model": "gpt-4o", "query": "hello"}))

    assert recorder._main_run_id == 42
    assert called["agent_name"] == "main"


@pytest.mark.asyncio
async def test_on_event_handles_tool_call(recorder, monkeypatch: pytest.MonkeyPatch):
    recorder._main_run_id = 1

    async def fake_create(**kwargs):
        return 99

    monkeypatch.setattr("crabagent.core.database.run_record_create", fake_create)

    await recorder.on_event(AgentEvent(type=EventType.TOOL_CALL, data={"name": "bash", "arguments": {"command": "ls"}}))

    assert len(recorder._main_tool_buf) == 1
    assert recorder._main_tool_buf[0]["args"] == {"command": "ls"}


@pytest.mark.asyncio
async def test_on_event_handles_tool_result(recorder, monkeypatch: pytest.MonkeyPatch):
    recorder._main_run_id = 1
    recorder._main_tool_buf = [{"name": "bash", "args": {}, "started_at": 0, "result_summary": None, "elapsed": 0}]

    await recorder.on_event(
        AgentEvent(type=EventType.TOOL_RESULT, data={"name": "bash", "result": "output", "id": "tc1"})
    )

    assert recorder._main_tool_buf[0]["result_summary"] == "output"


@pytest.mark.asyncio
async def test_tool_result_is_captured_for_linked_task_runs(recorder, monkeypatch: pytest.MonkeyPatch):
    recorder._main_run_id = 1
    recorder._main_tool_buf = [
        {
            "name": "write",
            "args": {"file_path": "/tmp/result.md"},
            "started_at": 0,
            "result_summary": None,
            "elapsed": 0,
        }
    ]
    recorder.link_task_run(42)
    captured: list[int] = []

    async def fake_capture(data, entry, run_id):
        captured.append(run_id)

    monkeypatch.setattr(recorder, "_capture_artifact", fake_capture)

    await recorder.on_event(AgentEvent(type=EventType.TOOL_RESULT, data={"name": "write", "result": "ok", "id": "tc1"}))

    assert captured == [1, 42]
    recorder.unlink_task_run(42)
    assert recorder._linked_task_run_ids == set()


@pytest.mark.asyncio
async def test_on_event_swallows_exceptions(recorder, monkeypatch: pytest.MonkeyPatch):
    async def fake_create(**kwargs):
        raise RuntimeError("DB error")

    monkeypatch.setattr("crabagent.core.database.run_record_create", fake_create)

    # Should not raise
    await recorder.on_event(AgentEvent(type=EventType.AGENT_START, data={"query": "test"}))


@pytest.mark.asyncio
async def test_on_event_ignores_unknown_event_type(recorder):
    await recorder.on_event(AgentEvent(type=EventType.TEXT_DELTA, data={"text": "hi"}))


@pytest.mark.asyncio
async def test_on_event_finalizes_agent_end(recorder, monkeypatch: pytest.MonkeyPatch):
    captured = {}
    recorder._main_run_id = 7
    recorder._main_started_at = 100.0
    recorder._main_tool_buf = [{"name": "bash", "result_summary": "done"}]

    monkeypatch.setattr("crabagent.core.agent.run_recorder._time", SimpleNamespace(time=lambda: 103.24))

    async def fake_finalize(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("crabagent.core.database.run_record_finalize", fake_finalize)

    await recorder.on_event(
        AgentEvent(
            type=EventType.AGENT_END,
            data={"tokens": 10, "iterations": 2, "result": "finished"},
        )
    )

    assert captured == {
        "run_id": 7,
        "status": "completed",
        "elapsed": 3.2,
        "tokens_used": 10,
        "iterations": 2,
        "tool_calls": [{"name": "bash", "result_summary": "done"}],
        "result_summary": "finished",
    }
    assert recorder._main_run_id is None
    assert recorder._main_tool_buf == []


@pytest.mark.asyncio
async def test_on_event_finalizes_cancelled_agent_end(recorder, monkeypatch: pytest.MonkeyPatch):
    captured = {}
    recorder._main_run_id = 8
    recorder._main_started_at = 100.0

    monkeypatch.setattr("crabagent.core.agent.run_recorder._time", SimpleNamespace(time=lambda: 101.0))

    async def fake_finalize(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("crabagent.core.database.run_record_finalize", fake_finalize)

    await recorder.on_event(
        AgentEvent(type=EventType.AGENT_END, data={"cancelled": True, "tokens": 4, "iterations": 1})
    )

    assert captured["status"] == "cancelled"
    assert captured["run_id"] == 8
    assert recorder._main_run_id is None


@pytest.mark.asyncio
async def test_on_event_marks_budget_exhausted(recorder, monkeypatch: pytest.MonkeyPatch):
    recorder._main_run_id = 12
    captured = {}

    async def fake_update(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("crabagent.core.database.run_record_update", fake_update)

    await recorder.on_event(AgentEvent(type=EventType.BUDGET_EXHAUSTED, data={"reason": "too many tokens"}))

    assert captured == {
        "run_id": 12,
        "result_summary": "Budget exhausted: too many tokens",
    }


@pytest.mark.asyncio
async def test_on_event_records_sub_agent_lifecycle(recorder, monkeypatch: pytest.MonkeyPatch):
    created = {}
    finalized = {}
    recorder._pipeline_run_id = 500

    monkeypatch.setattr("crabagent.core.agent.run_recorder._time", SimpleNamespace(time=lambda: 205.55))

    async def fake_create(**kwargs):
        created.update(kwargs)
        return 88

    async def fake_finalize(**kwargs):
        finalized.update(kwargs)

    monkeypatch.setattr("crabagent.core.database.run_record_create", fake_create)
    monkeypatch.setattr("crabagent.core.database.run_record_finalize", fake_finalize)

    await recorder.on_event(
        AgentEvent(
            type=EventType.SUB_AGENT_START,
            data={
                "sub_agent_id": "sub-1",
                "agent_name": "coder",
                "model": "gpt-4.1",
                "task": "write tests",
                "pipeline_step_id": "step-a",
            },
        )
    )
    await recorder.on_event(
        AgentEvent(
            type=EventType.SUB_AGENT_TOOL_CALL, data={"sub_agent_id": "sub-1", "name": "read", "args": {"file": "x"}}
        )
    )
    await recorder.on_event(
        AgentEvent(type=EventType.SUB_AGENT_TOOL_RESULT, data={"sub_agent_id": "sub-1", "name": "read", "result": "ok"})
    )
    await recorder.on_event(
        AgentEvent(
            type=EventType.SUB_AGENT_END, data={"sub_agent_id": "sub-1", "tokens": 8, "iterations": 1, "result": "done"}
        )
    )

    assert created["agent_name"] == "coder"
    assert created["parent_run_id"] == 500
    assert created["metadata"] == {"sub_agent_id": "sub-1", "pipeline_step_id": "step-a"}
    assert finalized["run_id"] == 88
    assert finalized["status"] == "completed"
    assert finalized["tokens_used"] == 8
    assert finalized["iterations"] == 1
    assert finalized["result_summary"] == "done"
    assert finalized["tool_calls"][0]["name"] == "read"
    assert finalized["tool_calls"][0]["result_summary"] == "ok"
    assert "sub-1" not in recorder._sub_runs


@pytest.mark.asyncio
async def test_on_event_creates_pipeline_run_and_marks_end(recorder, monkeypatch: pytest.MonkeyPatch):
    created = {}
    updated = {}

    async def fake_create(**kwargs):
        created.update(kwargs)
        return 321

    async def fake_update(**kwargs):
        updated.update(kwargs)

    monkeypatch.setattr("crabagent.core.database.run_record_create", fake_create)
    monkeypatch.setattr("crabagent.core.database.run_record_update", fake_update)

    await recorder.on_event(
        AgentEvent(
            type=EventType.PIPELINE_START,
            data={
                "step_ids": ["a", "b"],
                "step_agents": {"a": "coder", "b": "writer"},
                "step_tasks": {"a": "collect data"},
                "total_steps": 2,
            },
        )
    )
    await recorder.on_event(AgentEvent(type=EventType.PIPELINE_STEP_START, data={"step_id": "a", "started_at": 10.0}))
    await recorder.on_event(AgentEvent(type=EventType.PIPELINE_STEP_END, data={"step_id": "a", "elapsed": 1.5}))
    await recorder.on_event(
        AgentEvent(type=EventType.PIPELINE_END, data={"total": 2, "completed": ["a"], "failed": ["b"]})
    )

    assert created["agent_name"] == "pipeline"
    assert created["task_summary"] == "collect data"
    assert created["metadata"] == {
        "pipeline": True,
        "total_steps": 2,
        "step_ids": ["a", "b"],
        "step_agents": {"a": "coder", "b": "writer"},
    }
    assert updated == {
        "run_id": 321,
        "status": "failed",
        "result_summary": "1/2 steps succeeded",
    }
    assert recorder.pipeline_run_id is None
    assert recorder._pipeline_steps == {}


@pytest.mark.asyncio
async def test_pipeline_run_id_property(recorder):
    assert recorder.pipeline_run_id is None


# ── steps are saved while a run is still going (the timeline follows them live) ──


@pytest.mark.asyncio
async def test_main_run_steps_are_saved_before_the_run_ends(recorder, monkeypatch: pytest.MonkeyPatch):
    saved = []

    async def fake_update(run_id, **kwargs):
        saved.append((run_id, kwargs["tool_calls"]))

    monkeypatch.setattr("crabagent.core.database.run_record_update", fake_update)
    recorder._main_run_id = 5

    await recorder.on_event(AgentEvent(type=EventType.TOOL_CALL, data={"name": "bash", "arguments": {"command": "pytest -q"}}))
    await recorder.on_event(AgentEvent(type=EventType.TOOL_RESULT, data={"name": "bash", "result": "3 passed"}))

    assert [run_id for run_id, _ in saved] == [5, 5]
    assert saved[0][1][0]["result_summary"] is None  # visible as "running" while the command runs
    assert saved[1][1][0]["result_summary"] == "3 passed"


@pytest.mark.asyncio
async def test_a_failed_sub_agent_keeps_the_steps_it_took(recorder, monkeypatch: pytest.MonkeyPatch):
    saved, finalized = [], {}

    async def fake_create(**kwargs):
        return 7

    async def fake_update(run_id, **kwargs):
        saved.append(run_id)

    async def fake_finalize(**kwargs):
        finalized.update(kwargs)

    monkeypatch.setattr("crabagent.core.database.run_record_create", fake_create)
    monkeypatch.setattr("crabagent.core.database.run_record_update", fake_update)
    monkeypatch.setattr("crabagent.core.database.run_record_finalize", fake_finalize)

    await recorder.on_event(AgentEvent(type=EventType.SUB_AGENT_START, data={"sub_agent_id": "a", "agent_name": "coder"}))
    await recorder.on_event(AgentEvent(type=EventType.SUB_AGENT_TOOL_CALL,
                                       data={"sub_agent_id": "a", "name": "edit", "arguments": {"file_path": "x.py"}}))
    await recorder.on_event(AgentEvent(type=EventType.SUB_AGENT_ERROR, data={"sub_agent_id": "a", "error": "budget"}))

    assert saved == [7]
    assert finalized["status"] == "failed"
    assert finalized["tool_calls"][0]["args"] == {"file_path": "x.py"}


def _stub_sub_run_db(recorder, monkeypatch: pytest.MonkeyPatch):
    finalized, flushed = {}, []

    async def fake_create(**kwargs):
        return 7

    async def fake_update(run_id, **kwargs):
        pass

    async def fake_finalize(**kwargs):
        finalized.update(kwargs)

    async def fake_flush(run_id, agent_name, buf):
        flushed.append((run_id, agent_name, [t.get("name") for t in buf]))

    monkeypatch.setattr("crabagent.core.database.run_record_create", fake_create)
    monkeypatch.setattr("crabagent.core.database.run_record_update", fake_update)
    monkeypatch.setattr("crabagent.core.database.run_record_finalize", fake_finalize)
    monkeypatch.setattr(recorder, "_flush_tool_invocations", fake_flush)
    return finalized, flushed


@pytest.mark.asyncio
async def test_a_sub_agent_that_ran_out_of_steps_is_failed_but_keeps_its_summary_and_counts(
    recorder, monkeypatch: pytest.MonkeyPatch
):
    finalized, flushed = _stub_sub_run_db(recorder, monkeypatch)

    await recorder.on_event(AgentEvent(type=EventType.SUB_AGENT_START,
                                       data={"sub_agent_id": "a", "agent_name": "coder"}))
    await recorder.on_event(AgentEvent(type=EventType.SUB_AGENT_TOOL_CALL,
                                       data={"sub_agent_id": "a", "name": "edit", "arguments": {"file_path": "x.py"}}))
    await recorder.on_event(AgentEvent(type=EventType.SUB_AGENT_END, data={
        "sub_agent_id": "a", "tokens": 5000, "iterations": 80, "max_iterations": 80,
        "result": "Stopped early: ... wrote bench.py; checks not run yet", "budget_exhausted": True,
    }))

    assert finalized["status"] == "failed"
    assert finalized["error"] == "Ran out of steps (80/80) before finishing"
    assert finalized["tokens_used"] == 5000
    assert finalized["iterations"] == 80
    assert "checks not run yet" in finalized["result_summary"]
    assert flushed == [(7, "coder", ["edit"])]


@pytest.mark.asyncio
async def test_a_sub_agent_that_errors_keeps_its_token_and_step_counts(recorder, monkeypatch: pytest.MonkeyPatch):
    finalized, flushed = _stub_sub_run_db(recorder, monkeypatch)

    await recorder.on_event(AgentEvent(type=EventType.SUB_AGENT_START,
                                       data={"sub_agent_id": "a", "agent_name": "coder"}))
    await recorder.on_event(AgentEvent(type=EventType.SUB_AGENT_TOOL_CALL,
                                       data={"sub_agent_id": "a", "name": "bash", "arguments": {"command": "ls"}}))
    await recorder.on_event(AgentEvent(type=EventType.SUB_AGENT_ERROR,
                                       data={"sub_agent_id": "a", "error": "boom", "tokens": 1200, "iterations": 9}))

    assert finalized["status"] == "failed"
    assert finalized["tokens_used"] == 1200
    assert finalized["iterations"] == 9
    assert flushed == [(7, "coder", ["bash"])]


# ── a result finds its own call by id (parallel calls of one tool finish out of order) ──


def _stub_main_run_db(recorder, monkeypatch: pytest.MonkeyPatch):
    saved, finalized = [], {}

    async def fake_update(run_id, **kwargs):
        saved.append(kwargs["tool_calls"])

    async def fake_finalize(**kwargs):
        finalized.update(kwargs)

    monkeypatch.setattr("crabagent.core.database.run_record_update", fake_update)
    monkeypatch.setattr("crabagent.core.database.run_record_finalize", fake_finalize)
    recorder._main_run_id = 1
    return saved, finalized


async def _tool_call(recorder, name: str, call_id: str | None = None, args: dict | None = None):
    data = {"name": name, "arguments": args or {}}
    if call_id is not None:
        data["id"] = call_id
    await recorder.on_event(AgentEvent(type=EventType.TOOL_CALL, data=data))


async def _tool_result(recorder, name: str, result: str, call_id: str | None = None):
    data = {"name": name, "result": result}
    if call_id is not None:
        data["id"] = call_id
    await recorder.on_event(AgentEvent(type=EventType.TOOL_RESULT, data=data))


@pytest.mark.asyncio
@pytest.mark.parametrize("reversed_arrival", [True, False])
async def test_parallel_calls_of_the_same_tool_take_their_own_result_by_id(
    recorder, monkeypatch: pytest.MonkeyPatch, reversed_arrival: bool
):
    _stub_main_run_db(recorder, monkeypatch)

    await _tool_call(recorder, "code_search", "tc1", {"query": "first"})
    await _tool_call(recorder, "code_search", "tc2", {"query": "second"})
    results = [("code_search", "first-query-result", "tc1"), ("code_search", "second-query-result", "tc2")]
    for args in reversed(results) if reversed_arrival else results:
        await _tool_result(recorder, *args)

    buf = recorder._main_tool_buf
    # A swapped pairing records the second call's output under the first call.
    assert [t["result_summary"] for t in buf] == ["first-query-result", "second-query-result"]


@pytest.mark.asyncio
async def test_three_parallel_calls_pair_by_id_when_the_middle_one_finishes_last(
    recorder, monkeypatch: pytest.MonkeyPatch
):
    _stub_main_run_db(recorder, monkeypatch)

    await _tool_call(recorder, "code_search", "tc1")
    await _tool_call(recorder, "code_search", "tc2")
    await _tool_call(recorder, "code_search", "tc3")
    await _tool_result(recorder, "code_search", "result-one", "tc1")
    await _tool_result(recorder, "code_search", "result-three", "tc3")
    await _tool_result(recorder, "code_search", "result-two", "tc2")

    buf = recorder._main_tool_buf
    assert [t["result_summary"] for t in buf] == ["result-one", "result-two", "result-three"]


@pytest.mark.asyncio
async def test_results_without_ids_still_pair_by_name_as_before(recorder, monkeypatch: pytest.MonkeyPatch):
    """Old/replayed events carry no id; they keep the name + most-recent-pending pairing."""
    _stub_main_run_db(recorder, monkeypatch)

    await _tool_call(recorder, "code_search")
    await _tool_call(recorder, "code_search")
    await _tool_result(recorder, "code_search", "first-arrival")
    await _tool_result(recorder, "code_search", "second-arrival")

    buf = recorder._main_tool_buf
    assert [t["result_summary"] for t in buf] == ["second-arrival", "first-arrival"]
    assert all(t["result_summary"] is not None for t in buf)


@pytest.mark.asyncio
@pytest.mark.parametrize("idless_first", [True, False])
async def test_mixed_id_and_idless_calls_pair_out_of_order(
    recorder, monkeypatch: pytest.MonkeyPatch, idless_first: bool
):
    _stub_main_run_db(recorder, monkeypatch)

    await _tool_call(recorder, "code_search", "tc1")  # the call that knows its id
    await _tool_call(recorder, "code_search")  # an older-style call with no id
    if idless_first:
        await _tool_result(recorder, "code_search", "no-id-result")
        await _tool_result(recorder, "code_search", "id-result", "tc1")
    else:
        await _tool_result(recorder, "code_search", "id-result", "tc1")
        await _tool_result(recorder, "code_search", "no-id-result")

    buf = recorder._main_tool_buf
    assert [t["result_summary"] for t in buf] == ["id-result", "no-id-result"]


@pytest.mark.asyncio
async def test_a_result_whose_id_matches_no_pending_call_attaches_to_nothing(
    recorder, monkeypatch: pytest.MonkeyPatch
):
    _stub_main_run_db(recorder, monkeypatch)

    await _tool_call(recorder, "code_search", "tc1")
    await _tool_result(recorder, "code_search", "stray-result", "tc-unknown")

    buf = recorder._main_tool_buf
    assert len(buf) == 1  # no phantom entry
    assert buf[0]["result_summary"] is None  # the pending call stays pending


@pytest.mark.asyncio
async def test_the_call_id_is_recorder_bookkeeping_not_part_of_the_saved_json(
    recorder, monkeypatch: pytest.MonkeyPatch
):
    saved, finalized = _stub_main_run_db(recorder, monkeypatch)

    await _tool_call(recorder, "code_search", "tc1", {"query": "q"})
    await _tool_result(recorder, "code_search", "output", "tc1")
    await recorder.on_event(AgentEvent(type=EventType.AGENT_END, data={"result": "done"}))

    expected_keys = {"name", "args", "started_at", "result_summary", "elapsed", "ok", "reason_class"}
    for payload in [*saved, finalized["tool_calls"]]:
        assert payload  # the live save of the call itself, then of its result, then the finalize
        assert not any(key.startswith("_") for key in payload[0])
    assert set(finalized["tool_calls"][0]) == expected_keys


# ── the sub-agent event bridge forwards the call id, so sub-agent results pair by id too ──


def _stub_bridged_sub_agent(recorder, monkeypatch: pytest.MonkeyPatch):
    """A real spawn_sub_agent run with a stubbed loop, wired into a real RunRecorder."""
    from pathlib import Path

    from crabagent.core.agent import agents as agents_module
    from crabagent.core.agent.context import AgentContext

    agent_def = {
        "name": "scout", "display_name": "Scout", "role": "r", "goal": "g", "backstory": "b",
        "tool_permissions": {},
    }

    async def _ret(value):
        return value

    monkeypatch.setattr(agents_module, "get_agent", lambda *a, **k: _ret(agent_def))
    monkeypatch.setattr(agents_module, "_load_shared_context", lambda *a, **k: _ret(""))
    monkeypatch.setattr(agents_module, "load_sub_agent_model_map", lambda *a, **k: _ret({}))

    created, finalized = [], []

    async def fake_create(**kwargs):
        created.append(kwargs)
        return len(created)

    async def fake_update(run_id, **kwargs):
        pass

    async def fake_finalize(**kwargs):
        finalized.append(kwargs)

    async def fake_flush(run_id, agent_name, buf):
        pass

    monkeypatch.setattr("crabagent.core.database.run_record_create", fake_create)
    monkeypatch.setattr("crabagent.core.database.run_record_update", fake_update)
    monkeypatch.setattr("crabagent.core.database.run_record_finalize", fake_finalize)
    monkeypatch.setattr(recorder, "_flush_tool_invocations", fake_flush)

    parent = AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0})
    parent.event_bus.subscribe(recorder.on_event)
    agents_module._running_sub_agents.clear()
    return SimpleNamespace(module=agents_module, agent_def=agent_def, parent=parent,
                           created=created, finalized=finalized)


async def _run_bridged_sub_agent(recorder, monkeypatch: pytest.MonkeyPatch, sub_events: list):
    harness = _stub_bridged_sub_agent(recorder, monkeypatch)

    async def stub_run_agent(sub_context, task, *args, **kwargs):
        for event in sub_events:
            await sub_context.event_bus.emit(event)
        sub_context.messages.append({"role": "assistant", "content": "done"})
        return sub_context.messages

    monkeypatch.setattr(harness.module, "run_agent", stub_run_agent, raising=False)
    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", stub_run_agent)

    await harness.module.spawn_sub_agent(
        harness.parent, agent_name="scout", task="x", agent_def=harness.agent_def
    )
    assert len(harness.finalized) == 1
    return harness


def _sub_call(call_id: str | None, query: str) -> AgentEvent:
    data = {"name": "code_search", "arguments": {"query": query}}
    if call_id is not None:
        data["id"] = call_id
    return AgentEvent(type=EventType.TOOL_CALL, data=data)


def _sub_result(call_id: str | None, result: str) -> AgentEvent:
    data = {"name": "code_search", "result": result}
    if call_id is not None:
        data["id"] = call_id
    return AgentEvent(type=EventType.TOOL_RESULT, data=data)


@pytest.mark.asyncio
@pytest.mark.parametrize("reversed_arrival", [True, False])
async def test_a_sub_agents_parallel_calls_take_their_own_result_through_the_bridge(
    recorder, monkeypatch: pytest.MonkeyPatch, reversed_arrival: bool
):
    results = [_sub_result("tc1", "first-query-result"), _sub_result("tc2", "second-query-result")]
    harness = await _run_bridged_sub_agent(
        recorder,
        monkeypatch,
        [_sub_call("tc1", "first"), _sub_call("tc2", "second"),
         *(reversed(results) if reversed_arrival else results)],
    )

    steps = harness.finalized[0]["tool_calls"]
    assert [s["result_summary"] for s in steps] == ["first-query-result", "second-query-result"]
    assert [s["args"]["query"] for s in steps] == ["first", "second"]


@pytest.mark.asyncio
async def test_three_sub_agent_calls_pair_by_id_when_the_middle_one_finishes_last(
    recorder, monkeypatch: pytest.MonkeyPatch
):
    harness = await _run_bridged_sub_agent(
        recorder,
        monkeypatch,
        [
            _sub_call("tc1", "one"), _sub_call("tc2", "two"), _sub_call("tc3", "three"),
            _sub_result("tc1", "result-one"),
            _sub_result("tc3", "result-three"),
            _sub_result("tc2", "result-two"),
        ],
    )

    steps = harness.finalized[0]["tool_calls"]
    assert [s["result_summary"] for s in steps] == ["result-one", "result-two", "result-three"]


@pytest.mark.asyncio
async def test_a_sub_agent_result_without_an_id_still_pairs_by_name(recorder, monkeypatch: pytest.MonkeyPatch):
    harness = await _run_bridged_sub_agent(
        recorder,
        monkeypatch,
        [
            _sub_call(None, "first"), _sub_call(None, "second"),
            _sub_result(None, "first-arrival"), _sub_result(None, "second-arrival"),
        ],
    )

    steps = harness.finalized[0]["tool_calls"]
    assert [s["result_summary"] for s in steps] == ["second-arrival", "first-arrival"]
    assert all(s["result_summary"] is not None for s in steps)


@pytest.mark.asyncio
async def test_each_sub_agents_results_land_in_its_own_run(recorder, monkeypatch: pytest.MonkeyPatch):
    harness = _stub_bridged_sub_agent(recorder, monkeypatch)
    runs = [
        ("scout", [_sub_call("tc1", "a1"), _sub_call("tc2", "a2"),
                   _sub_result("tc2", "a-two"), _sub_result("tc1", "a-one")]),
        ("coder", [_sub_call("tc1", "b1"), _sub_call("tc2", "b2"),
                   _sub_result("tc1", "b-one"), _sub_result("tc2", "b-two")]),
    ]

    for agent_name, sub_events in runs:
        async def stub_run_agent(sub_context, task, *args, _events=sub_events, **kwargs):
            for event in _events:
                await sub_context.event_bus.emit(event)
            sub_context.messages.append({"role": "assistant", "content": "done"})
            return sub_context.messages

        monkeypatch.setattr(harness.module, "run_agent", stub_run_agent, raising=False)
        monkeypatch.setattr("crabagent.core.agent.loop.run_agent", stub_run_agent)
        await harness.module.spawn_sub_agent(
            harness.parent, agent_name=agent_name, task="x", agent_def=harness.agent_def
        )

    assert [c["agent_name"] for c in harness.created] == ["scout", "coder"]
    assert [[s["result_summary"] for s in run["tool_calls"]] for run in harness.finalized] == [
        ["a-one", "a-two"],
        ["b-one", "b-two"],
    ]
    # The id stays recorder bookkeeping on the sub-agent path too.
    assert not any(key.startswith("_") for run in harness.finalized for s in run["tool_calls"] for key in s)

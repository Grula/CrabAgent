"""Latest failing checks carried into the next coding brief (core/agent/recent_failures.py)."""

from pathlib import Path

import pytest

from crabagent.core.agent import agents as agents_module
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.recent_failures import MAX_AGE_SECONDS, STORE_KEY, failures_block, note_check_result
from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.event import AgentEvent, EventType

FAILED = "FAILED: 1 failed, 3 passed in 0.2s (pytest, exit code 1)\n\n1. tests/test_x.py::test_y\n   assert 1 == 2"


def _ctx() -> AgentContext:
    return AgentContext(workspace=Path("."))


def test_a_failure_is_kept_and_a_later_pass_clears_it():
    ctx = _ctx()
    note_check_result(ctx, "coder", "run_tests", FAILED)
    block = failures_block(ctx, "fix the parser")
    assert "Recent failures in this session" in block
    assert "from coder" in block and "tests/test_x.py::test_y" in block
    note_check_result(ctx, "coder", "run_tests", "PASSED: 4 passed in 0.1s (pytest)")
    assert failures_block(ctx, "fix the parser") == ""


def test_diagnostics_problems_and_clean_runs():
    ctx = _ctx()
    note_check_result(ctx, "coder", "diagnostics", "Checked 2 files: 1 problem.\na.py:3: [F821] Undefined name `x`")
    assert "Undefined name" in failures_block(ctx, "task")
    note_check_result(ctx, "coder", "diagnostics", "Checked 2 files: no problems.")
    assert failures_block(ctx, "task") == ""


@pytest.mark.parametrize("tool, result", [
    ("run_tests", "Error: unknown framework 'x'"),  # a usage error is not a test result
    ("diagnostics", "No files changed with edit/write in this run; pass paths"),
    ("bash", "FAILED: something"),
])
def test_other_results_are_ignored(tool, result):
    ctx = _ctx()
    note_check_result(ctx, "coder", tool, result)
    assert ctx.metadata.get(STORE_KEY, {}) == {}


def test_old_entries_and_ones_the_brief_already_quotes_are_left_out():
    ctx = _ctx()
    note_check_result(ctx, "coder", "run_tests", FAILED)
    at = ctx.metadata[STORE_KEY]["tests"]["at"]
    assert failures_block(ctx, "task", now=at + MAX_AGE_SECONDS + 1) == ""
    assert failures_block(ctx, "Fix this:\n" + FAILED.splitlines()[0]) == ""


@pytest.mark.asyncio
async def test_the_next_coder_brief_gets_the_last_failure(monkeypatch: pytest.MonkeyPatch):
    async def returns(value):
        return value

    monkeypatch.setattr(agents_module, "_load_shared_context", lambda *a, **k: returns(""))
    monkeypatch.setattr(agents_module, "load_sub_agent_model_map", lambda *a, **k: returns({}))
    briefs: list[str] = []
    results = iter([FAILED, None, "PASSED: 4 passed in 0.1s (pytest)", None])

    async def run_agent(sub_context, task, *args, **kwargs):
        briefs.append(task)
        result = next(results)
        if result:
            await sub_context.event_bus.emit(
                AgentEvent(type=EventType.TOOL_RESULT, data={"name": "run_tests", "result": result, "id": "1"})
            )
        sub_context.messages.append({"role": "assistant", "content": "done"})
        return sub_context.messages

    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", run_agent)
    agents_module._running_sub_agents.clear()

    def coder_registry():
        registry = ToolRegistry()
        for name in ("read", "edit"):
            registry.register(name=name, description=name, parameters={"type": "object", "properties": {}})(
                lambda context=None: ""
            )
        return registry

    parent = AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0})
    parent.tool_registry = coder_registry()
    coder = {"name": "coder", "display_name": "Coder", "role": "r", "goal": "g", "backstory": "",
             "tool_permissions": {}, "skills": []}

    await agents_module.spawn_sub_agent(parent, agent_name="coder", task="implement it", agent_def=coder)
    await agents_module.spawn_sub_agent(parent, agent_name="coder", task="fix the failures", agent_def=coder)
    assert "Recent failures" not in briefs[0]
    assert briefs[1].startswith("fix the failures") and "tests/test_x.py::test_y" in briefs[1]

    # A passing run clears it; the brief after that is clean again.
    await agents_module.spawn_sub_agent(parent, agent_name="coder", task="rerun", agent_def=coder)
    await agents_module.spawn_sub_agent(parent, agent_name="coder", task="next batch", agent_def=coder)
    assert briefs[3] == "next batch"

    # Read-only agents (no edit/write) never get the block.
    note_check_result(parent, "coder", "run_tests", FAILED)
    reviewer = {**coder, "name": "the_reviewer", "tool_permissions": {"edit": "deny"}}
    results = iter([None])
    await agents_module.spawn_sub_agent(parent, agent_name="the_reviewer", task="review", agent_def=reviewer)
    assert briefs[4] == "review"

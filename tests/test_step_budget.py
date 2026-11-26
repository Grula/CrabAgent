"""The per-step run-state note (loop.step_budget_note / with_step_note)."""

from pathlib import Path

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.loop import step_budget_note, with_step_note

_TOOL_TAIL = [
    {"role": "user", "content": "do it"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
    {"role": "tool", "content": "ok", "tool_call_id": "1"},
]


def _ctx(step: int, total: int = 80, sub: bool = True) -> AgentContext:
    ctx = AgentContext(workspace=Path("."), max_iterations=total)
    ctx.iteration = step
    if sub:
        ctx.metadata["_sub_agent_depth"] = 1
    return ctx


def test_sub_agents_see_their_step_every_step():
    assert step_budget_note(_ctx(34)) == "[Step 34 of 80]"


def test_main_agent_sees_nothing_until_the_wind_down():
    assert step_budget_note(_ctx(34, total=200, sub=False)) == ""
    assert "steps left" in step_budget_note(_ctx(191, total=200, sub=False))


def test_wind_down_starts_ten_steps_before_the_cap():
    assert "steps left" not in step_budget_note(_ctx(70))
    note = step_budget_note(_ctx(71))
    assert note.startswith("[Step 71 of 80] 10 steps left")
    assert "REMAINING" in note


def test_last_two_steps_say_report_now():
    for step in (79, 80):
        note = step_budget_note(_ctx(step))
        assert "make no new edits" in note and "final report" in note


def test_note_goes_in_a_trailing_message_and_leaves_history_alone():
    ctx = _ctx(72)
    original = [dict(m) for m in _TOOL_TAIL]
    out = with_step_note(_TOOL_TAIL, ctx)
    assert out[:-1] == original  # the tool result is unchanged, so its cache holds
    assert out[-1]["role"] == "user" and out[-1]["content"].startswith("[Step 72 of 80]")
    assert _TOOL_TAIL == original


def test_no_note_after_a_user_message():
    msgs = [{"role": "user", "content": "hi"}]
    assert with_step_note(msgs, _ctx(79)) is msgs

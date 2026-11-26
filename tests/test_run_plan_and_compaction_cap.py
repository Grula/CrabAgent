"""plan_update (run checklist kept through compaction) and the per-agent compaction cap."""

from pathlib import Path

import pytest

from crabagent.core.agent import compress
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.token_limits import compression_trigger_tokens
from crabagent.core.agent.tools.plan import RUN_PLAN_KEY, plan_update, render_run_plan
from crabagent.core.config import settings


def _ctx() -> AgentContext:
    return AgentContext(workspace=Path("."))


def test_plan_update_replaces_the_list_and_renders_it():
    ctx = _ctx()
    result = plan_update(
        [{"text": "write test", "status": "done"}, {"text": "fix  bug", "status": "in_progress"}], context=ctx
    )
    assert result.startswith("Checklist updated (1/2 done):")
    assert "[x] write test" in result and "[~] fix bug" in result
    plan_update([{"text": "only one", "status": "pending"}], context=ctx)
    assert ctx.metadata[RUN_PLAN_KEY] == [{"text": "only one", "status": "pending"}]
    assert plan_update([], context=ctx) == "Checklist cleared."
    assert render_run_plan(ctx) == ""


@pytest.mark.parametrize(
    "items, error",
    [
        ([{"text": "", "status": "done"}], "has no text"),
        ([{"text": "x", "status": "finished"}], "use one of"),
        (["x"], "must be an object"),
        ([{"text": "x", "status": "done"}] * 31, "at most 30"),
    ],
)
def test_plan_update_rejects_bad_items(items, error):
    ctx = _ctx()
    assert error in plan_update(items, context=ctx)
    assert RUN_PLAN_KEY not in ctx.metadata


@pytest.mark.asyncio
async def test_compaction_summary_carries_the_checklist(monkeypatch):
    async def fake_summary(*_args, **_kwargs):
        return "Earlier work summarised."

    monkeypatch.setattr(compress, "summarize_messages", fake_summary)
    ctx = _ctx()
    plan_update([{"text": "step one", "status": "done"}, {"text": "step two", "status": "pending"}], context=ctx)
    ctx.messages = [{"role": "user", "content": f"message {i} " + "x" * 400} for i in range(40)]
    await compress.compress_context(ctx, {}, "openai/gpt-4o")

    summary = ctx.messages[0]
    assert summary["role"] == "compress"
    assert summary["content"].startswith("Earlier work summarised.")
    assert "[x] step one" in summary["content"] and "[ ] step two" in summary["content"]


def test_agent_cap_replaces_the_global_cap(monkeypatch):
    monkeypatch.setattr(settings, "context_compression_max_tokens", 1_000_000)
    monkeypatch.setattr(settings, "context_compression_threshold", 0.8)
    monkeypatch.setattr("crabagent.core.agent.token_limits.get_model_token_limit", lambda _m: 1_000_000)
    assert compression_trigger_tokens("m") == 800_000
    assert compression_trigger_tokens("m", 200_000) == 200_000
    assert compression_trigger_tokens("m", None) == 800_000
    assert compression_trigger_tokens("m", 0) == 800_000
    # A cap above the window share never raises the trigger.
    assert compression_trigger_tokens("m", 900_000) == 800_000

"""Agent-loop hardening: provider-failure reporting, cancellation, stop hook,
token budget.

Mirrors the fake-provider fixtures of ``tests/test_agent_loop.py``.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import crabagent.core.agent.loop as loop
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.middlewares import MiddlewareChain
from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.event import EventType
from crabagent.serve.api import prompt as prompt_api


# ── fixtures / helpers (same shape as tests/test_agent_loop.py) ──────


class _Stream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk

        return gen()


def _stream(chunks):
    return _Stream(chunks)


def _chunk(delta, finish_reason=None, usage=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)], usage=usage)


def _delta(content=None, tool_calls=None, reasoning_content=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=reasoning_content)


def _tool_call(call_id: str, name: str, arguments: str, index: int = 0):
    return SimpleNamespace(id=call_id, index=index, function=SimpleNamespace(name=name, arguments=arguments))


def _usage():
    return SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        prompt_tokens_details=SimpleNamespace(cached_tokens=2),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
    )


def _async_return(value):
    async def inner(*args, **kwargs):
        return value

    return inner


def _fake_provider(**overrides):
    values = {
        "name": "mock",
        "provider_type": "openai",
        "api_key": "k",
        "base_url": "",
        "enabled": True,
        "extra": {},
    }
    values.update(overrides)

    async def fake(provider_name=None):
        return SimpleNamespace(**values)

    return fake


def _patch_litellm(monkeypatch, acompletion):
    monkeypatch.setattr(
        loop, "litellm", SimpleNamespace(acompletion=acompletion, exceptions=loop.litellm.exceptions)
    )
    monkeypatch.setattr("crabagent.core.proxy.resolve_llm_proxy", _async_return(""))


def _first(events, event_type):
    return next(event for event in events if event.type == event_type)


def _final_answer(text="done"):
    return _stream([_chunk(delta=_delta(content=text), finish_reason="stop", usage=_usage())])


# ── 1. provider-failure reporting ────────────────────────────────────


@pytest.mark.asyncio
async def test_pre_loop_missing_provider_emits_actionable_agent_error(monkeypatch: pytest.MonkeyPatch):
    context = AgentContext(workspace=Path.cwd(), model="gpt-4o", provider_name="broken", max_iterations=1)
    events = []
    context.event_bus.subscribe(lambda event: events.append(event))

    async def failing_provider(provider_name=None):
        raise ValueError(f"Configured provider '{provider_name}' is unavailable or disabled.")

    monkeypatch.setattr(loop, "_resolve_provider", failing_provider)

    messages = await loop.run_agent(context, "hello")

    # The run ends without raising, and the UI gets a real error instead of a
    # silent AGENT_END.
    assert messages[0]["role"] == "user"
    error = _first(events, EventType.AGENT_ERROR)
    text = error.data["error"]
    assert "broken" in text  # names the provider
    assert "Settings → Providers" in text  # actionable
    assert "Traceback" not in text and "File \"" not in text
    assert len(text) <= 200
    assert error.data["stage"] == "provider_setup"
    assert not any(event.type == EventType.AGENT_END for event in events)


@pytest.mark.asyncio
async def test_pre_loop_chatgpt_token_failure_emits_agent_error(monkeypatch: pytest.MonkeyPatch, tmp_path):
    context = AgentContext(workspace=Path.cwd(), model="gpt-4o", max_iterations=1)
    events = []
    context.event_bus.subscribe(lambda event: events.append(event))

    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(loop, "_resolve_provider", _fake_provider(name="chatgpt-work", provider_type="chatgpt"))

    await loop.run_agent(context, "hello")

    text = _first(events, EventType.AGENT_ERROR).data["error"]
    assert "chatgpt-work" in text
    assert "Settings → Providers" in text


def test_provider_error_message_maps_http_status_classes():
    auth = loop.litellm.exceptions.AuthenticationError(message="bad key", llm_provider="openai", model="gpt-4o")
    timeout = loop.litellm.exceptions.Timeout(message="timed out", llm_provider="openai", model="gpt-4o")
    unavailable = loop.litellm.exceptions.InternalServerError(
        message="upstream 502", llm_provider="openai", model="gpt-4o"
    )

    auth_text = loop._provider_error_message(auth, "zhipu-work", "glm-5")
    assert "API key" in auth_text
    assert "zhipu-work" in auth_text and "glm-5" in auth_text
    assert "Settings → Providers" in auth_text

    assert "timed out" in loop._provider_error_message(timeout, "zhipu-work", "glm-5")
    assert "transient" in loop._provider_error_message(unavailable, "zhipu-work", "glm-5")

    unknown = loop._provider_error_message(RuntimeError("Request failed"), "zhipu-work", "glm-5")
    assert "zhipu-work" in unknown and len(unknown) <= 200


@pytest.mark.asyncio
async def test_outer_run_failure_emits_agent_error_not_only_agent_end():
    context = AgentContext(workspace=Path.cwd(), model="gpt-4o")
    context.metadata["resolved_provider"] = "zhipu-work"
    context.metadata["resolved_model"] = "glm-5"
    events = []
    context.event_bus.subscribe(lambda event: events.append(event))

    exc = loop.litellm.exceptions.AuthenticationError(message="bad key", llm_provider="openai", model="glm-5")
    await prompt_api._report_run_failure(context, exc)

    error = _first(events, EventType.AGENT_ERROR)
    assert "zhipu-work" in error.data["error"]
    assert "Settings → Providers" in error.data["error"]
    assert error.data["stage"] == "agent_task"


@pytest.mark.asyncio
async def test_loop_fatal_authentication_error_names_provider_and_model(monkeypatch: pytest.MonkeyPatch):
    context = AgentContext(workspace=Path.cwd(), model="gpt-4o", max_iterations=1)
    events = []
    context.event_bus.subscribe(lambda event: events.append(event))

    async def fake_acompletion(**kwargs):
        raise loop.litellm.exceptions.AuthenticationError(message="bad key", llm_provider="openai", model="gpt-4o")

    monkeypatch.setattr(loop, "_resolve_provider", _fake_provider())
    _patch_litellm(monkeypatch, fake_acompletion)

    await loop.run_agent(context, "hello")

    error = _first(events, EventType.AGENT_ERROR)
    assert error.data["provider"] == "mock"
    assert error.data["model"] == "gpt-4o"
    assert "API key" in error.data["error"]
    assert "Settings → Providers" in error.data["error"]


# ── 2. cancellation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_mid_run_stops_without_error(monkeypatch: pytest.MonkeyPatch):
    registry = ToolRegistry()

    @registry.register(name="echo", description="echo", parameters={})
    async def echo(context=None):
        return "ok"

    context = AgentContext(workspace=Path.cwd(), tool_registry=registry, model="gpt-4o", max_iterations=5)
    events = []
    calls = {"count": 0}
    context.event_bus.subscribe(lambda event: events.append(event))

    async def fake_acompletion(**kwargs):
        calls["count"] += 1
        # The user hits Stop during the first round.
        context.request_cancel()
        return _stream(
            [
                _chunk(
                    delta=_delta(tool_calls=[_tool_call("c1", "echo", "{}")]),
                    finish_reason="tool_calls",
                    usage=_usage(),
                )
            ]
        )

    monkeypatch.setattr(loop, "_resolve_provider", _fake_provider())
    _patch_litellm(monkeypatch, fake_acompletion)

    await loop.run_agent(context, "hello")

    assert calls["count"] == 1  # stopped within one iteration
    assert context.iteration == 1
    assert context.metadata["_run_cancelled"] is True
    assert not any(event.type == EventType.AGENT_ERROR for event in events)
    end = _first(events, EventType.AGENT_END)
    assert end.data["cancelled"] is True
    assert "Stopped" in end.data["reason"]


@pytest.mark.asyncio
async def test_cancel_before_start_skips_the_model_call(monkeypatch: pytest.MonkeyPatch):
    context = AgentContext(workspace=Path.cwd(), model="gpt-4o", max_iterations=3)
    events = []
    context.event_bus.subscribe(lambda event: events.append(event))

    async def fake_acompletion(**kwargs):  # pragma: no cover - must not run
        raise AssertionError("model must not be called after cancellation")

    monkeypatch.setattr(loop, "_resolve_provider", _fake_provider())
    _patch_litellm(monkeypatch, fake_acompletion)
    context.request_cancel()

    await loop.run_agent(context, "hello")

    assert context.iteration == 0
    assert _first(events, EventType.AGENT_END).data["cancelled"] is True


@pytest.mark.asyncio
async def test_cancel_is_deferred_to_the_caller_that_emits_agent_end(monkeypatch: pytest.MonkeyPatch):
    context = AgentContext(workspace=Path.cwd(), model="gpt-4o", max_iterations=2)
    events = []
    context.event_bus.subscribe(lambda event: events.append(event))
    context.metadata["_caller_emits_agent_end"] = True

    async def fake_acompletion(**kwargs):  # pragma: no cover - must not run
        raise AssertionError("model must not be called after cancellation")

    monkeypatch.setattr(loop, "_resolve_provider", _fake_provider())
    _patch_litellm(monkeypatch, fake_acompletion)
    context.request_cancel()

    await loop.run_agent(context, "hello")

    # The API handler emits AGENT_END itself; the loop must not emit a second.
    assert not any(event.type == EventType.AGENT_END for event in events)
    assert context.metadata["_run_cancelled"] is True
    assert context.metadata["_cancel_reason"]


# ── 3. stop / continuation hook ─────────────────────────────────────


class _StopHook:
    name = "stop-hook"

    def __init__(self, injected: str | None):
        self.injected = injected
        self.seen: list[str] = []

    async def on_agent_stop(self, context, final_text):
        self.seen.append(final_text)
        injected, self.injected = self.injected, None
        return injected


@pytest.mark.asyncio
async def test_stop_hook_injects_one_extra_round_then_stops(monkeypatch: pytest.MonkeyPatch):
    hook = _StopHook("Please double-check the answer.")
    context = AgentContext(
        workspace=Path.cwd(),
        model="gpt-4o",
        max_iterations=6,
        middlewares=MiddlewareChain([hook]),
    )
    events = []
    rounds: list[list[dict]] = []
    context.event_bus.subscribe(lambda event: events.append(event))

    async def fake_acompletion(**kwargs):
        rounds.append(list(kwargs["messages"]))
        return _final_answer(f"answer {len(rounds)}")

    monkeypatch.setattr(loop, "_resolve_provider", _fake_provider())
    _patch_litellm(monkeypatch, fake_acompletion)

    messages = await loop.run_agent(context, "hello")

    assert len(rounds) == 2  # exactly one extra round
    assert any(m.get("content") == "Please double-check the answer." for m in rounds[1])
    assert hook.seen == ["answer 1", "answer 2"]
    assert context.iteration == 2
    assert messages[-1]["content"] == "answer 2"


@pytest.mark.asyncio
async def test_stop_hook_allowing_stop_keeps_default_behaviour(monkeypatch: pytest.MonkeyPatch):
    async def run(middlewares):
        context = AgentContext(workspace=Path.cwd(), model="gpt-4o", max_iterations=4, middlewares=middlewares)
        rounds: list[list[dict]] = []

        async def fake_acompletion(**kwargs):
            rounds.append(list(kwargs["messages"]))
            return _final_answer()

        monkeypatch.setattr(loop, "_resolve_provider", _fake_provider())
        _patch_litellm(monkeypatch, fake_acompletion)
        messages = await loop.run_agent(context, "hello")
        return context.iteration, rounds, [(m["role"], m.get("content")) for m in messages]

    baseline = await run(None)
    allowed = await run(MiddlewareChain([_StopHook(None)]))

    assert allowed == baseline
    assert allowed[0] == 1  # a single round, exactly as without middlewares


def test_run_stop_tolerates_middlewares_without_the_hook():
    class Plain:
        name = "plain"

    chain = MiddlewareChain([Plain()])

    import asyncio

    assert asyncio.run(chain.run_stop(AgentContext(workspace=Path.cwd()), "final")) is None


# ── 4. failure wording and the provider-setup exit ──────────────────


def test_provider_error_message_keeps_bad_request_detail_and_stays_neutral():
    bad = loop.litellm.exceptions.BadRequestError(
        message="temperature must be <= 2.0", llm_provider="openai", model="gpt-4o"
    )
    text = loop._provider_error_message(bad, "openai", "gpt-4o")
    assert text.startswith("Invalid request parameters:")
    assert "temperature must be <= 2.0" in text
    assert len(text) <= 200

    bug = RuntimeError("'NoneType' object has no attribute 'get'")
    text = loop._provider_error_message(bug, "openai", "gpt-4o")
    assert text.startswith("Agent request failed")
    assert "'NoneType' object has no attribute 'get'" in text
    assert "API key" not in text and "Settings → Providers" not in text
    assert len(text) <= 200


class _ConversationEndRecorder:
    name = "conversation-end-recorder"

    def __init__(self):
        self.calls = 0

    async def on_conversation_end(self, context):
        self.calls += 1


@pytest.mark.asyncio
async def test_provider_setup_failure_reports_provider_model_and_runs_end_hooks(
    monkeypatch: pytest.MonkeyPatch,
):
    recorder = _ConversationEndRecorder()
    context = AgentContext(
        workspace=Path.cwd(),
        model="gpt-4o",
        provider_name="broken",
        max_iterations=1,
        middlewares=MiddlewareChain([recorder]),
    )
    events = []
    context.event_bus.subscribe(lambda event: events.append(event))

    async def failing_provider(provider_name=None):
        raise ValueError(f"Configured provider '{provider_name}' is unavailable or disabled.")

    monkeypatch.setattr(loop, "_resolve_provider", failing_provider)

    await loop.run_agent(context, "hello")

    errors = [event for event in events if event.type == EventType.AGENT_ERROR]
    assert len(errors) == 1
    data = errors[0].data
    assert data["stage"] == "provider_setup"
    assert data["provider"] == "broken"
    assert data["model"] == "gpt-4o"
    assert context.metadata["_run_error"] is True
    # Every other exit fires the end hooks; this one must too, exactly once.
    assert recorder.calls == 1

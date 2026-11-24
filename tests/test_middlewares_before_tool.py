"""Tests for the optional ``on_before_tool`` middleware hook.

Mirrors the fake-provider fixtures of ``tests/test_agent_loop.py``: the first
model round emits one ``echo_tool`` call, the second returns a final answer.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import crabagent.core.agent.loop as loop
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.middlewares import MiddlewareChain
from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.event import EventType


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


_TOOL_ARGS = {"text": "hi", "count": 2}
_TOOL_CALL_ID = "call_1"


def _make_registry(received: list[dict]) -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register(
        name="echo_tool",
        description="echo",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}, "count": {"type": "integer"}},
        },
    )
    async def echo_tool(text: str, count: int = 0, context=None):
        received.append({"text": text, "count": count})
        return f"echo:{text}:{count}"

    return registry


def _patch_provider(monkeypatch: pytest.MonkeyPatch) -> dict:
    """First LLM round emits the tool call, the second one answers."""
    rounds = {"n": 0}

    async def fake_acompletion(**kwargs):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return _stream(
                [
                    _chunk(
                        delta=_delta(tool_calls=[_tool_call(_TOOL_CALL_ID, "echo_tool", json.dumps(_TOOL_ARGS))]),
                        finish_reason=None,
                    )
                ]
            )
        return _stream([_chunk(delta=_delta(content="done"), finish_reason="stop", usage=_usage())])

    async def fake_provider(provider_name=None):
        return SimpleNamespace(name="mock", provider_type="openai", api_key="k", base_url="", enabled=True)

    monkeypatch.setattr(loop, "_resolve_provider", fake_provider)
    monkeypatch.setattr(
        loop, "litellm", SimpleNamespace(acompletion=fake_acompletion, exceptions=loop.litellm.exceptions)
    )
    monkeypatch.setattr("crabagent.core.proxy.resolve_llm_proxy", _async_return(""))
    return rounds


async def _run(monkeypatch: pytest.MonkeyPatch, middlewares=None, received: list | None = None):
    received = [] if received is None else received
    context = AgentContext(
        workspace=Path.cwd(),
        tool_registry=_make_registry(received),
        model="gpt-4o",
        max_iterations=3,
        middlewares=middlewares,
    )
    events: list = []
    context.event_bus.subscribe(lambda event: events.append(event))
    rounds = _patch_provider(monkeypatch)
    messages = await loop.run_agent(context, "hello")
    return SimpleNamespace(messages=messages, events=events, rounds=rounds, received=received)


def _tool_messages(messages) -> list[dict]:
    return [msg for msg in messages if msg.get("role") == "tool"]


# ── tests ────────────────────────────────────────────────────────────


class _NoHookMiddleware:
    """Implements other hooks but not ``on_before_tool``."""

    name = "no-hook"

    async def on_conversation_start(self, context):
        pass

    async def on_conversation_end(self, context):
        pass


@pytest.mark.asyncio
async def test_without_the_hook_the_message_list_is_unchanged(monkeypatch: pytest.MonkeyPatch):
    plain = await _run(monkeypatch, middlewares=None)
    with_chain = await _run(monkeypatch, middlewares=MiddlewareChain([_NoHookMiddleware()]))

    assert plain.received == [{"text": "hi", "count": 2}]
    assert with_chain.received == plain.received
    # Byte-identical history: dropping the hook must not change one field.
    assert json.dumps(with_chain.messages) == json.dumps(plain.messages)


@pytest.mark.asyncio
async def test_blocking_middleware_skips_the_handler_and_answers_the_call(monkeypatch: pytest.MonkeyPatch):
    class Blocker:
        name = "blocker"

        async def on_before_tool(self, context, tool_name, args):
            return "policy says no"

    run = await _run(monkeypatch, middlewares=MiddlewareChain([Blocker()]))

    # The handler never ran, but the model still got a tool answer.
    assert run.received == []
    tool_msgs = _tool_messages(run.messages)
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "Tool call blocked: policy says no"
    assert tool_msgs[0]["tool_call_id"] == _TOOL_CALL_ID

    result_events = [event for event in run.events if event.type == EventType.TOOL_RESULT]
    assert len(result_events) == 1
    assert result_events[0].data["ok"] is False
    assert result_events[0].data["reason_class"] == "blocked"
    assert result_events[0].data["id"] == _TOOL_CALL_ID

    # Blocking is not an error: the loop continues and finishes normally.
    assert not any(event.type == EventType.AGENT_ERROR for event in run.events)
    assert run.rounds["n"] == 2
    assert any(msg.get("role") == "assistant" and msg.get("content") == "done" for msg in run.messages)


@pytest.mark.asyncio
async def test_hook_returning_none_allows_the_call(monkeypatch: pytest.MonkeyPatch):
    seen = []

    class Allow:
        name = "allow"

        async def on_before_tool(self, context, tool_name, args):
            seen.append(tool_name)
            return None

    run = await _run(monkeypatch, middlewares=MiddlewareChain([Allow()]))

    assert seen == ["echo_tool"]
    assert run.received == [{"text": "hi", "count": 2}]
    tool_msgs = _tool_messages(run.messages)
    assert tool_msgs[0]["content"] == "echo:hi:2"
    assert not any(event.type == EventType.AGENT_ERROR for event in run.events)


@pytest.mark.asyncio
async def test_raising_middleware_is_treated_as_allow(monkeypatch: pytest.MonkeyPatch):
    class Broken:
        name = "broken"

        async def on_before_tool(self, context, tool_name, args):
            raise RuntimeError("boom-deliberate")

    run = await _run(monkeypatch, middlewares=MiddlewareChain([Broken()]))

    assert run.received == [{"text": "hi", "count": 2}]
    assert _tool_messages(run.messages)[0]["content"] == "echo:hi:2"
    assert not any(event.type == EventType.AGENT_ERROR for event in run.events)
    assert any(msg.get("role") == "assistant" and msg.get("content") == "done" for msg in run.messages)


@pytest.mark.asyncio
async def test_hook_receives_the_tool_name_and_the_parsed_args(monkeypatch: pytest.MonkeyPatch):
    seen = []

    class Recorder:
        name = "recorder"

        async def on_before_tool(self, context, tool_name, args):
            seen.append((tool_name, dict(args)))
            return None

    run = await _run(monkeypatch, middlewares=MiddlewareChain([Recorder()]))

    assert seen == [("echo_tool", {"text": "hi", "count": 2})]
    # The hook saw a JSON-decoded dict, not the raw argument string.
    assert isinstance(seen[0][1]["count"], int)
    # Inspecting the args must not disturb what the handler receives.
    assert run.received == [{"text": "hi", "count": 2}]


@pytest.mark.asyncio
async def test_first_blocking_middleware_wins_and_later_ones_are_not_consulted(monkeypatch: pytest.MonkeyPatch):
    consulted = []

    class First:
        name = "first"

        async def on_before_tool(self, context, tool_name, args):
            consulted.append("first")
            return "first says no"

    class Second:
        name = "second"

        async def on_before_tool(self, context, tool_name, args):
            consulted.append("second")
            return "second says no"

    run = await _run(monkeypatch, middlewares=MiddlewareChain([First(), Second()]))

    # The chain short-circuits on the first non-empty reason.
    assert consulted == ["first"]
    assert run.received == []
    tool_msgs = _tool_messages(run.messages)
    assert tool_msgs[0]["content"] == "Tool call blocked: first says no"
    result_event = next(event for event in run.events if event.type == EventType.TOOL_RESULT)
    assert result_event.data["reason_class"] == "blocked"

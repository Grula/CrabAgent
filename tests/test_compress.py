from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest

from crabagent.core.agent import compress
from crabagent.core.agent.context import AgentContext
from crabagent.core.event import EventType
from crabagent.core.fts import extract_indexable_text


class FakeChunk:
    def __init__(self, content=None, reasoning=None, choices=None, usage=None):
        self.usage = usage
        if choices is None:
            delta = SimpleNamespace(content=content, reasoning_content=reasoning)
            self.choices = [SimpleNamespace(delta=delta, finish_reason=None if content else "stop")]
        else:
            self.choices = choices


class FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c

        return gen()


@pytest.mark.asyncio
async def test_compress_context_skips_when_too_few_messages():
    context = AgentContext(workspace=Path.cwd(), messages=[{"role": "user", "content": "a"}])

    await compress.compress_context(context, {}, "gpt-4o")

    assert len(context.messages) == 1


@pytest.mark.asyncio
async def test_compress_context_replaces_messages_with_summary(monkeypatch: pytest.MonkeyPatch):
    context = AgentContext(
        workspace=Path.cwd(),
        system_prompt="sys",
        messages=(
            [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
                {"role": "assistant", "content": "four"},
                {"role": "user", "content": "five"},
                {"role": "assistant", "content": "six"},
                {"role": "user", "content": "seven"},
                {"role": "assistant", "content": "eight"},
                {"role": "user", "content": "nine"},
                {"role": "assistant", "content": "ten"},
            ]
        ),
    )
    events = []
    context.event_bus.subscribe(lambda event: events.append(event))

    async def fake_acompletion(**kwargs):
        return FakeStream([FakeChunk(content="SUMMARY TEXT"), FakeChunk(content=None)])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    await compress.compress_context(context, {}, "gpt-4o")

    roles = [msg["role"] for msg in context.messages]
    assert roles[0] == "compress"
    assert roles[1] == "assistant"
    assert context.messages[0]["content"] == "SUMMARY TEXT"
    assert any(event.type == EventType.COMPRESS_START for event in events)
    assert any(event.type == EventType.CONTEXT_COMPRESSED for event in events)
    assert context.total_tokens == 0


def _tool_loop(reasoning: bool) -> list[dict]:
    """A user brief followed by four tool-call rounds, as a thinking model produces them."""
    messages: list[dict] = [{"role": "user", "content": "brief"}]
    for i in range(4):
        call = {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]}
        if reasoning:
            call["reasoning_content"] = f"thinking {i}"
        messages += [call, {"role": "tool", "tool_call_id": f"c{i}", "content": f"out {i}"}]
    return messages


@pytest.mark.asyncio
async def test_a_compacted_thinking_run_sends_reasoning_on_every_assistant_message(monkeypatch):
    # 2026-11-23: two DeepSeek runs crossed the 150k compaction cap and their next request failed
    # with "The `reasoning_content` in the thinking mode must be passed back to the API": the
    # acknowledgement inserted after the summary was the only assistant message without it.
    from crabagent.core.agent.loop import _build_messages

    context = AgentContext(workspace=Path.cwd(), system_prompt="sys", messages=_tool_loop(reasoning=True))

    async def fake_acompletion(**kwargs):
        return FakeStream([FakeChunk(content="SUMMARY")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(compress.settings, "context_keep_recent", 4)

    await compress.compress_context(context, {}, "deepseek-flash")

    sent = _build_messages(context)
    assistants = [m for m in sent if m["role"] == "assistant"]
    assert len(assistants) == 3  # the acknowledgement plus the two kept tool-call rounds
    assert all(m.get("reasoning_content") for m in assistants)


@pytest.mark.asyncio
async def test_messages_with_no_content_still_compress(monkeypatch):
    # 2026-11-23: an empty ask_question reply (content=None) made every compaction fail with
    # "object of type 'NoneType' has no len()", so a DeepSeek chat grew to 230k tokens unchecked.
    messages = [{"role": "user", "content": "brief"}]
    for i in range(4):
        messages += [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"c{i}", "type": "function", "function": {"name": "ask_question", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": None},
        ]
    context = AgentContext(workspace=Path.cwd(), system_prompt="sys", messages=messages)
    sent = []

    async def fake_acompletion(**kwargs):
        sent.append(kwargs["messages"])
        return FakeStream([FakeChunk(content="SUMMARY")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(compress.settings, "context_keep_recent", 4)

    await compress.compress_context(context, {}, "deepseek-flash")

    assert context.messages[0] == {"role": "compress", "content": "SUMMARY"}
    # Empty content must not turn into the literal text "None".
    assert not any(str(m.get("content")).startswith("None") for m in sent[0])


@pytest.mark.asyncio
async def test_a_compacted_run_without_reasoning_sends_no_reasoning_field(monkeypatch):
    context = AgentContext(workspace=Path.cwd(), system_prompt="sys", messages=_tool_loop(reasoning=False))

    async def fake_acompletion(**kwargs):
        return FakeStream([FakeChunk(content="SUMMARY")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(compress.settings, "context_keep_recent", 4)

    await compress.compress_context(context, {}, "gpt-4o")

    assert not any("reasoning_content" in m for m in context.messages)


@pytest.mark.asyncio
async def test_a_first_compaction_after_switching_to_a_thinking_model_completes(monkeypatch):
    # 2026-11-26 audit item 27: a non-thinking chat switched to a DeepSeek thinking
    # model has no reasoning_content anywhere yet. The summariser answers without
    # one, so the ack must be sent without the field instead of an invalid payload.
    context = AgentContext(workspace=Path.cwd(), system_prompt="sys", messages=_tool_loop(reasoning=False))

    async def fake_acompletion(**kwargs):
        return FakeStream([FakeChunk(content="SUMMARY")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(compress.settings, "context_keep_recent", 4)

    await compress.compress_context(context, {}, "deepseek/deepseek-v4-pro")

    from crabagent.core.agent.loop import _build_messages

    assert context.messages[0]["content"] == "SUMMARY"
    assert not any("reasoning_content" in m for m in _build_messages(context))


@pytest.mark.asyncio
async def test_the_ack_does_not_claim_reasoning_the_compaction_summarised_away(monkeypatch):
    # The ack is sent next to the *kept* messages, so it must mirror them: here every
    # reasoning-carrying message falls into the summarised part, and the ack must not
    # become the only assistant message carrying reasoning_content.
    messages = [
        {"role": "user", "content": "brief"},
        {"role": "assistant", "content": "thinking", "reasoning_content": "t0"},
        {"role": "tool", "tool_call_id": "c0", "content": "out"},
        {"role": "user", "content": "one"},
        {"role": "user", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "user", "content": "four"},
    ]
    context = AgentContext(workspace=Path.cwd(), system_prompt="sys", messages=messages)

    async def fake_acompletion(**kwargs):
        return FakeStream([FakeChunk(content="SUMMARY")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(compress.settings, "context_keep_recent", 4)

    await compress.compress_context(context, {}, "deepseek-flash")

    assert context.messages[0]["content"] == "SUMMARY"
    assert not any("reasoning_content" in m for m in context.messages)


@pytest.mark.asyncio
async def test_compress_context_preserves_recent_messages(monkeypatch: pytest.MonkeyPatch):
    context = AgentContext(
        workspace=Path.cwd(),
        messages=[{"role": "user", "content": str(index)} for index in range(10)],
    )

    async def fake_acompletion(**kwargs):
        return FakeStream([FakeChunk(content="SUMMARY")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(compress.settings, "context_keep_recent", 3)

    await compress.compress_context(context, {}, "gpt-4o")

    assert context.messages[0]["content"] == "SUMMARY"
    assert [message["content"] for message in context.messages[-3:]] == ["7", "8", "9"]


@pytest.mark.asyncio
async def test_compression_converts_tool_protocol_to_plain_text(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}
    messages = [
        {"role": "user", "content": "Inspect the project"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "function": {"name": "bash", "arguments": "{\"command\":\"pwd\"}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "/workspace"},
    ]

    async def fake_acompletion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return FakeStream([FakeChunk(content="SUMMARY")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    summary = await compress.summarize_messages(
        messages,
        system_prompt="sys",
        llm_params={},
        model="gpt-4o",
    )

    assert summary == "SUMMARY"
    request_messages = captured["messages"]
    assert all("tool_calls" not in message for message in request_messages)
    assert all(message["role"] != "tool" for message in request_messages)
    assert any(message["content"] == "[Tool call: bash]" for message in request_messages)
    assert any("[Tool result: bash]" in message["content"] for message in request_messages)


@pytest.mark.asyncio
async def test_compress_context_emits_failed_event_on_error(monkeypatch: pytest.MonkeyPatch):
    context = AgentContext(
        workspace=Path.cwd(),
        messages=[{"role": "user", "content": str(i)} for i in range(20)],
    )
    events = []
    context.event_bus.subscribe(lambda event: events.append(event))

    async def fake_acompletion(**kwargs):
        raise ValueError("bad")

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    await compress.compress_context(context, {}, "gpt-4o")

    compressed_event = next(event for event in events if event.type == EventType.CONTEXT_COMPRESSED)
    assert compressed_event.data.get("failed") is True
    assert len(context.messages) == 20


@pytest.mark.asyncio
async def test_compress_context_retries_on_rate_limit(monkeypatch: pytest.MonkeyPatch):
    context = AgentContext(
        workspace=Path.cwd(),
        messages=[{"role": "user", "content": str(i)} for i in range(20)],
    )
    call_count = {"n": 0}

    async def fake_acompletion(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise litellm.exceptions.RateLimitError(
                message="slow down",
                llm_provider="openai",
                model="gpt-4o",
            )
        return FakeStream([FakeChunk(content="OK SUMMARY")])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(compress.asyncio, "sleep", _async_noop)

    await compress.compress_context(context, {}, "gpt-4o")

    assert call_count["n"] == 2
    assert context.messages[0]["content"] == "OK SUMMARY"


async def _async_noop(*args, **kwargs):
    return None


def test_extract_indexable_text_skips_images_and_large_payloads():
    content = (
        '[{"type":"text","text":"keep this"},{"type":"image_url","image_url":{"url":"data:image/png;base64,AAAA"}}]'
    )
    assert extract_indexable_text(content) == "keep this"
    assert extract_indexable_text("x" * 256_001) == ""

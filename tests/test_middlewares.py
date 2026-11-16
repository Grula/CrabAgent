"""Tests for middleware chain, compress middleware, title middleware."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.middlewares import MiddlewareChain
from crabagent.core.agent.middlewares.compress_middleware import CompressMiddleware
from crabagent.core.agent.middlewares.title_middleware import (
    TitleMiddleware,
    _clean_title,
    _first_assistant_text,
    _first_user_text,
    _is_default_title,
)


# ── MiddlewareChain ───────────────────────────────────────────────────


class TestMiddlewareChain:
    @pytest.mark.asyncio
    async def test_run_start_calls_all_middlewares(self):
        calls = []

        class MwA:
            name = "a"

            async def on_conversation_start(self, context):
                calls.append("a_start")

        class MwB:
            name = "b"

            async def on_conversation_start(self, context):
                calls.append("b_start")

        chain = MiddlewareChain([MwA(), MwB()])
        await chain.run_start(AgentContext(workspace=Path.cwd()))

        assert calls == ["a_start", "b_start"]

    @pytest.mark.asyncio
    async def test_run_start_tolerates_sync_hooks(self):
        calls = []

        class MwSync:
            name = "sync"

            def on_conversation_start(self, context):
                calls.append("sync_start")

        chain = MiddlewareChain([MwSync()])
        await chain.run_start(AgentContext(workspace=Path.cwd()))
        assert calls == ["sync_start"]

    @pytest.mark.asyncio
    async def test_run_start_swallows_exceptions(self):
        class MwBad:
            name = "bad"

            async def on_conversation_start(self, context):
                raise RuntimeError("boom")

        class MwGood:
            name = "good"

            async def on_conversation_start(self, context):
                pass

        chain = MiddlewareChain([MwBad(), MwGood()])
        # Should not raise
        await chain.run_start(AgentContext(workspace=Path.cwd()))

    @pytest.mark.asyncio
    async def test_run_start_skips_missing_hook(self):
        class MwNoHook:
            name = "nohook"
            # No on_conversation_start method

        chain = MiddlewareChain([MwNoHook()])
        await chain.run_start(AgentContext(workspace=Path.cwd()))

    @pytest.mark.asyncio
    async def test_run_before_llm_passes_messages_through_chain(self):
        class MwAdd:
            name = "add"

            async def before_llm_call(self, context, messages):
                return messages + [{"role": "user", "content": "added"}]

        chain = MiddlewareChain([MwAdd()])
        result = await chain.run_before_llm(
            AgentContext(workspace=Path.cwd()),
            [{"role": "user", "content": "original"}],
        )

        assert len(result) == 2
        assert result[1]["content"] == "added"

    @pytest.mark.asyncio
    async def test_run_before_llm_handles_none_return(self):
        class MwNoop:
            name = "noop"

            async def before_llm_call(self, context, messages):
                return None

        chain = MiddlewareChain([MwNoop()])
        original = [{"role": "user", "content": "x"}]
        result = await chain.run_before_llm(AgentContext(workspace=Path.cwd()), original)
        assert result is original

    @pytest.mark.asyncio
    async def test_run_end_calls_in_reverse(self):
        calls = []

        class MwA:
            name = "a"

            async def on_conversation_end(self, context):
                calls.append("a_end")

        class MwB:
            name = "b"

            async def on_conversation_end(self, context):
                calls.append("b_end")

        chain = MiddlewareChain([MwA(), MwB()])
        await chain.run_end(AgentContext(workspace=Path.cwd()))

        assert calls == ["b_end", "a_end"]  # reversed

    def test_add_appends_middleware(self):
        chain = MiddlewareChain()
        mw = SimpleNamespace(name="x")
        chain.add(mw)
        assert len(chain.middlewares) == 1

    def test_empty_chain_has_no_middlewares(self):
        chain = MiddlewareChain()
        assert chain.middlewares == []


# ── CompressMiddleware ────────────────────────────────────────────────


class TestCompressMiddleware:
    @pytest.mark.asyncio
    async def test_returns_messages_unchanged_when_below_threshold(self):
        mw = CompressMiddleware()
        ctx = AgentContext(workspace=Path.cwd(), model="gpt-4o", total_tokens=100)
        ctx.metadata["_resolved_model"] = "gpt-4o"
        messages = [{"role": "user", "content": "hi"}]

        result = await mw.before_llm_call(ctx, messages)

        assert result is messages

    @pytest.mark.asyncio
    async def test_returns_messages_when_llm_params_missing(self):
        mw = CompressMiddleware()
        ctx = AgentContext(workspace=Path.cwd(), model="gpt-4o", total_tokens=999999)
        ctx.metadata["_resolved_model"] = "gpt-4o"
        # _llm_params not set

        result = await mw.before_llm_call(ctx, [{"role": "user", "content": "hi"}])
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_on_conversation_start_is_noop(self):
        mw = CompressMiddleware()
        await mw.on_conversation_start(AgentContext(workspace=Path.cwd()))

    @pytest.mark.asyncio
    async def test_on_conversation_end_is_noop(self):
        mw = CompressMiddleware()
        await mw.on_conversation_end(AgentContext(workspace=Path.cwd()))


# ── TitleMiddleware helpers ───────────────────────────────────────────


class TestTitleHelpers:
    def test_first_user_text_finds_string_content(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello world"},
        ]
        assert _first_user_text(msgs) == "hello world"

    def test_first_user_text_finds_list_content(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "from blocks"}]},
        ]
        assert _first_user_text(msgs) == "from blocks"

    def test_first_user_text_returns_empty_for_no_user(self):
        assert _first_user_text([{"role": "assistant", "content": "x"}]) == ""

    def test_first_assistant_text_finds_content(self):
        msgs = [{"role": "assistant", "content": "response"}]
        assert _first_assistant_text(msgs) == "response"

    def test_first_assistant_text_returns_empty_for_no_assistant(self):
        assert _first_assistant_text([{"role": "user", "content": "x"}]) == ""

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("", True),
            ("New Chat", True),
            ("New Conversation", True),
            ("Conversation 1", True),
            ("Chat #3", True),
            ("My Custom Title", False),
            ("Debugging API Issue", False),
        ],
    )
    def test_is_default_title(self, title, expected):
        assert _is_default_title(title) is expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  Hello World  ", "Hello World"),
            ('"Quoted Title"', "Quoted Title"),
            ("Title: My Title", "My Title"),
            ("Title: Sample", "Sample"),
            ("Hello!!!", "Hello"),
            ("Multiple    Spaces", "Multiple Spaces"),
        ],
    )
    def test_clean_title(self, raw, expected):
        assert _clean_title(raw) == expected


# ── TitleMiddleware ───────────────────────────────────────────────────


class TestTitleMiddleware:
    @pytest.mark.asyncio
    async def test_on_conversation_end_skips_without_session_id(self):
        mw = TitleMiddleware()
        ctx = AgentContext(workspace=Path.cwd())
        ctx.metadata["session_id"] = ""
        # Should not raise
        await mw.on_conversation_end(ctx)

    @pytest.mark.asyncio
    async def test_on_conversation_end_skips_without_messages(self):
        mw = TitleMiddleware()
        ctx = AgentContext(workspace=Path.cwd())
        ctx.metadata["session_id"] = "s1"
        ctx.messages = []
        await mw.on_conversation_end(ctx)

    @pytest.mark.asyncio
    async def test_on_conversation_end_skips_with_many_messages(self):
        mw = TitleMiddleware()
        ctx = AgentContext(workspace=Path.cwd())
        ctx.metadata["session_id"] = "s1"
        ctx.metadata["_resolved_model"] = "gpt-4o"
        ctx.messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        ctx.messages.append({"role": "assistant", "content": "resp"})
        await mw.on_conversation_end(ctx)

    @pytest.mark.asyncio
    async def test_before_llm_call_returns_messages_unchanged(self):
        mw = TitleMiddleware()
        ctx = AgentContext(workspace=Path.cwd())
        msgs = [{"role": "user", "content": "x"}]
        result = await mw.before_llm_call(ctx, msgs)
        assert result is msgs


# ── Conversation titles: placeholders, delegation cleanup, safe writes ──

import asyncio as _asyncio

import crabagent.core.agent.middlewares.title_middleware as title_mod
import crabagent.core.database as _db


class TestTitleSource:
    def test_plain_message_is_kept(self):
        assert title_mod.title_source_text("  Can we   do an audit\nof the project?  ") == "Can we do an audit of the project?"

    def test_delegate_task_uses_the_original_request(self):
        msg = '[delegate_task] agent_name="coder" task="fix it"\n\nOriginal request: @coder please fix the login bug'
        assert title_mod.title_source_text(msg) == "please fix the login bug"

    def test_delegate_task_from_the_team_dialog_uses_the_task(self):
        msg = '[delegate_task] agent_name="the_reviewer" task="Review the payment module for bugs"'
        assert title_mod.title_source_text(msg) == "Review the payment module for bugs"

    def test_delegate_parallel_joins_the_tasks(self):
        msg = '[delegate_parallel] tasks=[{"agent_name": "a", "task": "Write tests"}, {"agent_name": "b", "task": "Update docs"}]'
        assert title_mod.title_source_text(msg) == "Write tests; Update docs"

    def test_attachment_list_is_dropped(self):
        msg = "Summarize this\n\n📎 Attachments:\n- /tmp/a.pdf (a.pdf, 10KB)\n\nRead the attachments first"
        assert title_mod.title_source_text(msg) == "Summarize this"

    def test_provisional_title_cuts_at_a_word(self):
        title = title_mod.provisional_title("Can we do audit of the project, including structure, security and tests?")
        assert title.endswith("…") and len(title) <= 51
        assert not title[:-1].endswith(" ")
        assert "[delegate" not in title_mod.provisional_title('[delegate_task] agent_name="x" task="Ship the release"')


class TestReplaceableTitle:
    def test_defaults_and_placeholders_are_replaceable(self):
        first = "Hey can u review this code? Whatever you find, list it by severity please"
        assert title_mod.is_replaceable_title("", first)
        assert title_mod.is_replaceable_title("New", first)
        assert title_mod.is_replaceable_title(title_mod.provisional_title(first), first)
        # format written by earlier versions of the web UI and the TUI
        assert title_mod.is_replaceable_title(first[:50] + "...", first)

    def test_a_name_the_user_chose_is_kept(self):
        assert not title_mod.is_replaceable_title("Code review for auth", "Hey can u review this code?")


class TestFinalizeTitle:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("**Audit Project Security**", "Audit Project Security"),
            ("Title: Fix flaky test.\nBecause the user asked…", "Fix flaky test"),
            ("", ""),
            ("x", ""),
        ],
    )
    def test_finalize(self, raw, expected):
        assert title_mod._finalize_title(raw) == expected

    def test_long_output_is_cut_at_a_word(self):
        title = title_mod._finalize_title("word " * 40)
        assert len(title) <= 80 and not title.endswith(" ")


@pytest.fixture
async def titles_db(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(_db, "async_session_factory", factory)
    yield factory
    await engine.dispose()


async def _add_conversation(factory, session_id: str, title: str, auto_titled: bool = False) -> None:
    async with factory() as session:
        session.add(_db.Conversation(session_id=session_id, user_id=1, title=title, auto_titled=auto_titled))
        await session.commit()


async def _title_of(factory, session_id: str):
    from sqlalchemy import select

    async with factory() as session:
        row = await session.execute(
            select(_db.Conversation.title, _db.Conversation.auto_titled).where(_db.Conversation.session_id == session_id)
        )
        return tuple(row.first())


async def _wait_for_titles():
    for _ in range(50):
        if not title_mod._IN_FLIGHT:
            return
        await _asyncio.sleep(0.01)


class TestScheduleTitle:
    @pytest.mark.asyncio
    async def test_writes_the_title_and_tells_the_listener(self, titles_db, monkeypatch):
        async def fake_generate(user_text, assistant_text, model, provider_name):
            assert user_text == "Can we do an audit of the project"
            return "Project audit"

        monkeypatch.setattr(title_mod, "generate_title", fake_generate)
        await _add_conversation(titles_db, "s1", "Can we do an audit of the project")
        heard: list[str] = []

        async def listener(title: str) -> None:
            heard.append(title)

        assert title_mod.schedule_title(
            session_id="s1", user_text="Can we do an audit of the project", model="m", provider_name=None,
            expected_title="Can we do an audit of the project", on_title=listener,
        )
        await _wait_for_titles()

        assert await _title_of(titles_db, "s1") == ("Project audit", True)
        assert heard == ["Project audit"]

    @pytest.mark.asyncio
    async def test_a_rename_while_the_model_writes_is_kept(self, titles_db, monkeypatch):
        async def fake_generate(*args):
            # The user renames the chat before the model answers.
            async with titles_db() as session:
                conv = await session.get(_db.Conversation, 1)
                conv.title = "My name for it"
                await session.commit()
            return "Model title"

        monkeypatch.setattr(title_mod, "generate_title", fake_generate)
        await _add_conversation(titles_db, "s2", "placeholder")
        heard: list[str] = []

        async def listener(title: str) -> None:
            heard.append(title)

        title_mod.schedule_title(session_id="s2", user_text="x y", model="m", provider_name=None,
                                 expected_title="placeholder", on_title=listener)
        await _wait_for_titles()

        assert await _title_of(titles_db, "s2") == ("My name for it", False)
        assert heard == []

    @pytest.mark.asyncio
    async def test_only_one_request_per_session_at_a_time(self, titles_db, monkeypatch):
        release = _asyncio.Event()

        async def slow_generate(*args):
            await release.wait()
            return "Title"

        monkeypatch.setattr(title_mod, "generate_title", slow_generate)
        await _add_conversation(titles_db, "s3", "p")
        kwargs = dict(session_id="s3", user_text="hello there", model="m", provider_name=None, expected_title="p")
        assert title_mod.schedule_title(**kwargs)
        await _asyncio.sleep(0)
        assert not title_mod.schedule_title(**kwargs)
        release.set()
        await _wait_for_titles()
        assert await _title_of(titles_db, "s3") == ("Title", True)


class TestTitleMiddlewareFallback:
    @pytest.mark.asyncio
    async def test_titles_a_long_tool_heavy_first_turn(self, titles_db, monkeypatch):
        """One user turn with many tool messages used to be skipped (> 8 messages)."""
        first = '[delegate_task] agent_name="the_reviewer" task="Review the payment module"'

        async def fake_generate(user_text, assistant_text, model, provider_name):
            assert user_text == "Review the payment module"
            return "Payment module review"

        monkeypatch.setattr(title_mod, "generate_title", fake_generate)
        await _add_conversation(titles_db, "s4", first[:50] + "...")
        ctx = AgentContext(workspace=Path.cwd())
        ctx.metadata["session_id"] = "s4"
        ctx.model = "m"
        ctx.messages = [{"role": "user", "content": first}]
        for i in range(12):
            ctx.messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i)}]})
            ctx.messages.append({"role": "tool", "content": "ok", "tool_call_id": str(i)})
        ctx.messages.append({"role": "assistant", "content": "Found three issues."})

        await TitleMiddleware().on_conversation_end(ctx)
        await _wait_for_titles()

        assert await _title_of(titles_db, "s4") == ("Payment module review", True)

    @pytest.mark.asyncio
    async def test_leaves_auto_titled_and_renamed_chats_alone(self, titles_db, monkeypatch):
        async def boom(*args):
            raise AssertionError("must not ask the model")

        monkeypatch.setattr(title_mod, "generate_title", boom)
        await _add_conversation(titles_db, "s5", "Done already", auto_titled=True)
        await _add_conversation(titles_db, "s6", "My own name")
        for sid in ("s5", "s6"):
            ctx = AgentContext(workspace=Path.cwd())
            ctx.metadata["session_id"] = sid
            ctx.model = "m"
            ctx.messages = [{"role": "user", "content": "hello world"}, {"role": "assistant", "content": "hi"}]
            await TitleMiddleware().on_conversation_end(ctx)
        await _wait_for_titles()
        assert await _title_of(titles_db, "s5") == ("Done already", True)
        assert await _title_of(titles_db, "s6") == ("My own name", False)


# ── ReflectMiddleware passes the BARE model to the reflect LLM helpers ──

import asyncio as _reflect_asyncio

import crabagent.core.agent.middlewares.reflect_middleware as reflect_mw_mod
import crabagent.core.config as _reflect_config
import crabagent.core.database as _reflect_db


class TestReflectMiddlewareBareModel:
    @pytest.mark.asyncio
    async def test_on_conversation_end_does_not_prefix_openai_on_the_model(self, monkeypatch):
        """A gemini run must hand "gemini-2.5-pro" to reflect.py, which prefixes it itself.

        Adding "openai/" here defeats resolve_model_for_provider() ("/" short-circuit).
        """
        captured_models: list[str] = []
        scheduled: list = []

        async def fake_llm_reflect_lesson(**kwargs):
            captured_models.append(kwargs["model"])
            return None

        async def fake_llm_extract_user_preferences(**kwargs):
            captured_models.append(kwargs["model"])
            return []

        async def noop_async(*args, **kwargs):
            return None

        async def empty_rows(*args, **kwargs):
            return []

        class _FakeAsyncio:
            """Capture the fire-and-forget task instead of running it detached."""

            @staticmethod
            def create_task(coro):
                scheduled.append(coro)

            @staticmethod
            async def gather(*aws, **kwargs):
                return await _reflect_asyncio.gather(*aws, **kwargs)

        monkeypatch.setattr(_reflect_config.settings, "memory_auto_extract", True)
        # _has_signal is called as a module-level function inside the method.
        monkeypatch.setattr(reflect_mw_mod, "_has_signal", lambda context: True)
        # These are imported into reflect_middleware's namespace: patch the import site.
        monkeypatch.setattr(reflect_mw_mod, "llm_reflect_lesson", fake_llm_reflect_lesson)
        monkeypatch.setattr(reflect_mw_mod, "llm_extract_user_preferences", fake_llm_extract_user_preferences)
        monkeypatch.setattr(reflect_mw_mod, "rule_extract_lesson", lambda **kwargs: None)
        monkeypatch.setattr(reflect_mw_mod, "persist_lesson", noop_async)
        monkeypatch.setattr(reflect_mw_mod, "persist_preferences", noop_async)
        monkeypatch.setattr(reflect_mw_mod, "asyncio", _FakeAsyncio)
        # Imported inside the background coroutine from the database module.
        monkeypatch.setattr(_reflect_db, "agent_memory_get_by_agent", empty_rows)
        monkeypatch.setattr(_reflect_db, "agent_memory_list_all", empty_rows)

        context = SimpleNamespace(
            metadata={
                "user_id": 1,
                "_resolved_model": "gemini-2.5-pro",
                "session_id": "s",
                "workspace_path": "/tmp",
                "_run_elapsed": 1.0,
            },
            provider_name="gemini",
            current_agent="default",
            iteration=3,
            max_iterations=10,
            total_tokens=500,
            model="gemini-2.5-pro",
            messages=[
                {"role": "user", "content": "explain how to sort a list"},
                {"role": "assistant", "content": "use sorted()"},
            ],
        )

        await reflect_mw_mod.ReflectMiddleware().on_conversation_end(context)

        assert len(scheduled) == 1, "expected exactly one background reflect task"
        await scheduled[0]

        assert captured_models == ["gemini-2.5-pro", "gemini-2.5-pro"]
        assert "openai/gemini-2.5-pro" not in captured_models

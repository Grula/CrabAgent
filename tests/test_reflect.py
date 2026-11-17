from __future__ import annotations

from types import SimpleNamespace

import pytest

from crabagent.core.agent import reflect


class TestReflectHelpers:
    def test_classify_task_returns_general_for_unknown_text(self):
        assert reflect.classify_task("just chatting about weather") == "general"

    def test_extract_response_text_prefers_message_content(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer", reasoning_content="thinking"))]
        )

        assert reflect._extract_response_text(response) == "answer"

    def test_extract_response_text_uses_reasoning_fallback(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="", reasoning_content="hidden response final answer"))]
        )

        assert "final answer" in reflect._extract_response_text(response)

    def test_is_generic_praise_detects_known_phrases(self):
        assert reflect._is_generic_praise("Successfully completed the task in time") is True
        assert reflect._is_generic_praise("Use smaller commits before refactors") is False


class TestLLMReflectLesson:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_provider(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(reflect, "_resolve_llm_params", _async_return(None))

        result = await reflect.llm_reflect_lesson(
            agent_name="coder",
            task="fix bug",
            result="done",
            task_category="code",
            stats={"iterations": 2, "tokens": 10, "elapsed": 1.2},
            model="gpt-4o",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_parses_insight_and_uses_failed_approach_for_errors(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(reflect, "_resolve_llm_params", _async_return({"api_key": "k"}))

        async def fake_acompletion(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Category: code\nInsight: Check file existence before destructive writes.",
                            reasoning_content="",
                        )
                    )
                ]
            )

        monkeypatch.setattr(reflect, "litellm", SimpleNamespace(acompletion=fake_acompletion))

        result = await reflect.llm_reflect_lesson(
            agent_name="coder",
            task="fix bug",
            result="",
            task_category="code",
            stats={"iterations": 2, "tokens": 10, "elapsed": 1.2},
            model="gpt-4o",
            error_msg="boom",
        )

        assert result["category"] == "failed_approach"
        assert result["task_category"] == "code"
        assert "Check file existence" in result["content"]
        assert result["importance"] == 0.7  # below the 0.85 of lessons people kept

    @pytest.mark.asyncio
    async def test_rejects_generic_praise(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(reflect, "_resolve_llm_params", _async_return({"api_key": "k"}))

        async def fake_acompletion(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Insight: Successfully completed the task", reasoning_content=""))]
            )

        monkeypatch.setattr(reflect, "litellm", SimpleNamespace(acompletion=fake_acompletion))

        result = await reflect.llm_reflect_lesson(
            agent_name="coder",
            task="fix bug",
            result="done",
            task_category="code",
            stats={"iterations": 2, "tokens": 10, "elapsed": 1.2},
            model="gpt-4o",
        )

        assert result is None


def _answer(content: str, finish_reason: str = "stop", reasoning: str = ""):
    async def fake_acompletion(**kwargs):
        fake_acompletion.prompt = kwargs["messages"][0]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, reasoning_content=reasoning),
        )])

    return fake_acompletion


async def _reflect(monkeypatch, fake, **overrides):
    monkeypatch.setattr(reflect, "_resolve_llm_params", _async_return({"api_key": "k"}))
    monkeypatch.setattr(reflect, "litellm", SimpleNamespace(acompletion=fake))
    kwargs = dict(agent_name="coder", task="fix bug", result="done", task_category="code",
                  stats={"iterations": 15, "tokens": 10, "elapsed": 1.2}, model="gpt-4o")
    kwargs.update(overrides)
    return await reflect.llm_reflect_lesson(**kwargs)


class TestLessonQuality:
    """Every case here is a kind of junk that was found saved as a lesson."""

    @pytest.mark.asyncio
    async def test_a_good_lesson_from_a_long_run_starts_below_kept_ones(self, monkeypatch):
        fake = _answer("Category: code\nInsight: Piping pytest through tail hides its exit code; use set -o pipefail.")
        lesson = await _reflect(monkeypatch, fake)
        assert lesson["importance"] == 0.6  # long runs used to get 0.8 and crowd out the rest
        assert "When in doubt, answer NONE" in fake.prompt
        assert "different project" in fake.prompt

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content", [
        "NONE", "None.",
        "Category: code\nInsight: <a single concrete, actionable sentence>",
        "Category: code\nInsight: the lesson, as one complete sentence",
        "Category: code\nInsight: For byte-for-byte read-only lint sweeps, pass `--no-cache` (or set `RUFF",
        "Category: code\nInsight: Fix it.",
        "The user asks to extract one lesson. We should consider the task...\nOtherwise respond in EX",
    ])
    async def test_junk_is_not_saved(self, monkeypatch, content):
        assert await _reflect(monkeypatch, _answer(content)) is None

    @pytest.mark.asyncio
    async def test_an_answer_cut_off_by_the_token_limit_is_not_saved(self, monkeypatch):
        fake = _answer("Category: code\nInsight: Always check the exit code of piped commands.", finish_reason="length")
        assert await _reflect(monkeypatch, fake) is None

    @pytest.mark.asyncio
    async def test_reasoning_without_an_answer_is_not_saved(self, monkeypatch):
        fake = _answer("", reasoning="The user asks: extract ONE concrete lesson. Insight: something useful.")
        assert await _reflect(monkeypatch, fake) is None

    @pytest.mark.asyncio
    async def test_existing_lessons_are_shown_to_avoid_repeats(self, monkeypatch):
        fake = _answer("NONE")
        await _reflect(monkeypatch, fake, existing_lessons=[{"content": "Prove a failing test reaches its assertion."}])
        assert "Prove a failing test reaches its assertion." in fake.prompt


class TestSavePaths:
    @pytest.mark.asyncio
    async def test_persist_lesson_accepts_the_workspace_the_main_agent_passes(self, monkeypatch):
        saved = {}

        async def upsert(**kwargs):
            saved.update(kwargs)

        monkeypatch.setattr("crabagent.core.database.agent_memory_get_by_agent", _async_return([]))
        monkeypatch.setattr("crabagent.core.database.agent_memory_upsert", upsert)
        lesson = {"key": "k", "content": "Use set -o pipefail when piping test output.", "memory_type": "agent_lesson"}

        assert await reflect.persist_lesson(1, "architect", lesson, source_session="s", workspace_path="/proj")
        assert saved["workspace_path"] == "/proj"

    @pytest.mark.asyncio
    async def test_sub_agent_lessons_go_through_persist_lesson_with_known_lessons(self, monkeypatch):
        from crabagent.core.agent import agents as agents_module

        known = [{"key": "a", "content": "Patch the name where the code under test imports it."}]
        seen = {}

        async def reflect_fn(**kwargs):
            seen["existing"] = kwargs["existing_lessons"]
            return {"key": "k", "content": "Lesson text that is long enough.", "memory_type": "agent_lesson"}

        async def persist(user_id, agent_name, lesson, source_session="", workspace_path=""):
            seen["persisted"] = (agent_name, lesson["content"], workspace_path)
            return True

        monkeypatch.setattr("crabagent.core.database.agent_memory_get_by_agent", _async_return(known))
        monkeypatch.setattr(agents_module, "_llm_reflect_lesson", reflect_fn)
        monkeypatch.setattr(reflect, "persist_lesson", persist)

        await agents_module._reflect_and_save_lesson(
            user_id=1, agent_name="tester", task="t", result="r", task_category="code",
            stats={"iterations": 3, "tokens": 1, "elapsed": 1.0}, model="m", provider_name=None,
            session_id="s", workspace="/proj",
        )

        assert seen["existing"] == known
        assert seen["persisted"] == ("tester", "Lesson text that is long enough.", "/proj")


class TestPreferenceExtraction:
    @pytest.mark.asyncio
    async def test_skips_short_conversations(self):
        result = await reflect.llm_extract_user_preferences("too short", model="gpt-4o")

        assert result == []

    @pytest.mark.asyncio
    async def test_extracts_multiple_preferences(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(reflect, "_resolve_llm_params", _async_return({"api_key": "k"}))

        async def fake_acompletion(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "Category: language\nPreference: User prefers Chinese for chat.\n\n"
                                "Category: format\nPreference: User wants JSON output when possible."
                            ),
                            reasoning_content="",
                        )
                    )
                ]
            )

        monkeypatch.setattr(reflect, "litellm", SimpleNamespace(acompletion=fake_acompletion))

        convo = "User: Please reply in Chinese and output JSON format when possible." * 20
        prefs = await reflect.llm_extract_user_preferences(convo, model="gpt-4o", max_prefs=2)

        assert len(prefs) == 2
        assert prefs[0]["category"] == "language"
        assert "Chinese" in prefs[0]["content"]
        assert prefs[1]["category"] == "format"

    @pytest.mark.asyncio
    async def test_filters_bad_or_too_short_preferences(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(reflect, "_resolve_llm_params", _async_return({"api_key": "k"}))

        async def fake_acompletion(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "Category: general\nPreference: none\n\n"
                                "Category: style\nPreference: short\n\n"
                                "Category: style\nPreference: User prefers concise summaries."
                            ),
                            reasoning_content="",
                        )
                    )
                ]
            )

        monkeypatch.setattr(reflect, "litellm", SimpleNamespace(acompletion=fake_acompletion))

        convo = "User keeps asking for concise summaries and says no fluff please." * 10
        prefs = await reflect.llm_extract_user_preferences(convo, model="gpt-4o")

        assert len(prefs) == 1
        assert prefs[0]["category"] == "style"


class TestPersistenceHelpers:
    @pytest.mark.asyncio
    async def test_persist_lesson_skips_meta_content(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(reflect, "looks_like_meta_lesson", lambda content: True)

        ok = await reflect.persist_lesson(
            user_id=1,
            agent_name="coder",
            lesson={"key": "k1", "content": "meta lesson", "category": "effective_strategy"},
        )

        assert ok is False

    @pytest.mark.asyncio
    async def test_persist_lesson_reuses_key_on_string_dedup(self, monkeypatch: pytest.MonkeyPatch):
        saved = {}

        async def fake_get_by_agent(*args, **kwargs):
            return [{"key": "existing-key", "content": "Use small commits", "task_category": "code"}]

        async def fake_upsert(**kwargs):
            saved.update(kwargs)

        monkeypatch.setattr("crabagent.core.database.agent_memory_get_by_agent", fake_get_by_agent)
        monkeypatch.setattr("crabagent.core.database.agent_memory_upsert", fake_upsert)
        monkeypatch.setattr(reflect, "_enforce_lesson_cap", _async_return(None))
        monkeypatch.setattr(reflect, "looks_like_meta_lesson", lambda content: False)
        monkeypatch.setattr(reflect, "normalize_lesson_text", lambda text: text.lower())
        monkeypatch.setattr(reflect, "string_similarity_score", lambda a, b: 0.99)

        ok = await reflect.persist_lesson(
            user_id=1,
            agent_name="coder",
            lesson={
                "key": "new-key",
                "content": "Use small commits",
                "category": "effective_strategy",
                "task_category": "code",
            },
            source_session="sess-1",
        )

        assert ok is True
        assert saved["key"] == "existing-key"
        assert saved["source_session"] == "sess-1"

    @pytest.mark.asyncio
    async def test_persist_preferences_counts_only_successes(self, monkeypatch: pytest.MonkeyPatch):
        calls = {"count": 0}

        async def fake_upsert(**kwargs):
            calls["count"] += 1
            if kwargs["key"] == "bad":
                raise RuntimeError("fail")

        monkeypatch.setattr("crabagent.core.database.agent_memory_upsert", fake_upsert)

        saved = await reflect.persist_preferences(
            1,
            [
                {"key": "good", "content": "Use Chinese", "category": "language"},
                {"key": "bad", "content": "Broken pref", "category": "general"},
            ],
            source_session="sess-1",
        )

        assert calls["count"] == 2
        assert saved == 1


def _async_return(value):
    async def inner(*args, **kwargs):
        return value

    return inner

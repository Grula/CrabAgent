from types import SimpleNamespace

from crabagent.core.agent import token_limits
from crabagent.core.agent.token_limits import DEFAULT_MODEL_TOKEN_LIMITS, get_model_token_limit, is_vision_model


class TestTokenLimits:
    def test_exact_match(self):
        assert get_model_token_limit("deepseek-chat") == 1_000_000
        assert get_model_token_limit("gpt-4o") == 128_000
        assert get_model_token_limit("gpt-4") == 8_000
        assert get_model_token_limit("o3") == 200_000
        assert get_model_token_limit("qwen-turbo") == 1_000_000
        assert get_model_token_limit("glm-5.3") == 1_000_000
        assert get_model_token_limit("glm-5.3-flash") == 1_000_000

    def test_prefix_match(self):
        assert get_model_token_limit("claude-sonnet-4-custom") == 200_000
        assert get_model_token_limit("gpt-4o-custom") == 128_000

    def test_litellm_prefix_stripped(self):
        assert get_model_token_limit("openai/gpt-4o") == 128_000
        assert get_model_token_limit("openai/deepseek-chat") == 1_000_000

    def test_custom_override_from_settings(self, monkeypatch):
        monkeypatch.setattr(token_limits, "settings", SimpleNamespace(model_token_limits={"gpt-4o": 999}))

        assert get_model_token_limit("gpt-4o") == 999

    def test_unknown_model_fallback(self):
        assert get_model_token_limit("totally-unknown-model") == 1_000_000

    def test_gpt_61_sol_context(self):
        assert get_model_token_limit("gpt-6.1-sol") == 1_000_000
        assert get_model_token_limit("chatgpt/gpt-6.1-sol") == 1_000_000

    def test_future_model_fallback(self):
        assert get_model_token_limit("chatgpt/gpt-6.2-sol") == 1_000_000
        assert get_model_token_limit("openai/new-model") == 1_000_000

    def test_new_model_custom_override(self, monkeypatch):
        monkeypatch.setattr(token_limits, "settings", SimpleNamespace(model_token_limits={"gpt-6.1-sol": 500_000}))

        assert get_model_token_limit("chatgpt/gpt-6.1-sol") == 500_000

    def test_all_models_have_positive_limit(self):
        for model, limit in DEFAULT_MODEL_TOKEN_LIMITS.items():
            assert limit > 0, f"{model} has non-positive limit {limit}"

    def test_is_vision_model_rejects_exact_unsupported_models(self):
        assert is_vision_model("deepseek-chat") is False
        assert is_vision_model("openai/qwen-turbo") is False

    def test_is_vision_model_rejects_unsupported_prefixes(self):
        assert is_vision_model("deepseek-anything") is False
        assert is_vision_model("openai/o1-preview-extended") is False

    def test_is_vision_model_accepts_supported_models(self):
        assert is_vision_model("gpt-4o") is True
        assert is_vision_model("openai/claude-sonnet-4-20250514") is True

    def test_is_vision_model_accepts_glm_53_flash(self):
        assert is_vision_model("glm-5.3-flash") is True
        assert is_vision_model("openai/GLM-5.3-Flash") is True


class TestCompressionTrigger:
    """Compaction starts at the lower of 80% of the window and a fixed cap."""

    def _settings(self, cap):
        return SimpleNamespace(
            model_token_limits={}, context_compression_threshold=0.8, context_compression_max_tokens=cap
        )

    def test_a_large_window_is_compacted_at_the_cap(self, monkeypatch):
        monkeypatch.setattr(token_limits, "settings", self._settings(150_000))

        assert token_limits.compression_trigger_tokens("deepseek-chat") == 150_000

    def test_a_small_window_keeps_its_own_threshold(self, monkeypatch):
        monkeypatch.setattr(token_limits, "settings", self._settings(150_000))

        assert token_limits.compression_trigger_tokens("gpt-4") == 6_400

    def test_a_cap_of_zero_turns_it_off(self, monkeypatch):
        monkeypatch.setattr(token_limits, "settings", self._settings(0))

        assert token_limits.compression_trigger_tokens("deepseek-chat") == 800_000

    def test_the_default_cap_is_deepseeks_1m_window(self):
        from crabagent.core.config import Settings

        assert Settings.model_fields["context_compression_max_tokens"].default == 1_000_000

    def test_with_the_default_cap_deepseek_compacts_at_80_percent_of_1m(self):
        from crabagent.core.agent.token_limits import compression_trigger_tokens

        assert compression_trigger_tokens("deepseek-chat") == 800_000

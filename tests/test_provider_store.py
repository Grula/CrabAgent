from __future__ import annotations

import crabagent.core.provider_store as provider_store
from crabagent.core.provider_store import ProviderInfo, build_litellm_params, resolve_model_for_provider


def test_build_litellm_params_for_chatgpt_provider_omits_api_key():
    provider = ProviderInfo(
        name="gptplus",
        display_name="ChatGPT",
        provider_type="chatgpt",
        api_key="oauth-managed",
        base_url="",
        is_default=False,
        enabled=True,
        extra={},
    )

    params = build_litellm_params(provider)

    assert params == {}


def test_build_litellm_params_for_openai_compatible_provider_includes_api_key_and_base():
    provider = ProviderInfo(
        name="opencodeGO",
        display_name="OpenCode Go",
        provider_type="opencode-go",
        api_key="sk-test",
        base_url="https://example.com/v1",
        is_default=True,
        enabled=True,
        extra={},
    )

    params = build_litellm_params(provider)

    # OpenCode Go requires a stable x-opencode-session header on every request.
    headers = params.pop("extra_headers")
    assert params == {
        "api_key": "sk-test",
        "api_base": "https://example.com/v1",
        "custom_llm_provider": "openai",
    }
    assert headers["x-opencode-session"]
    assert headers["User-Agent"].startswith("crabagent")


def test_opencode_session_id_is_used_when_provided():
    provider = ProviderInfo(
        name="opencodeGO",
        display_name="OpenCode Go",
        provider_type="opencode-go",
        api_key="sk-test",
        base_url="https://example.com/v1",
        is_default=False,
        enabled=True,
        extra={},
    )

    params = build_litellm_params(provider, session_id="conv-123")
    headers = params["extra_headers"]

    assert headers["x-opencode-session"] == "conv-123"


def test_non_opencode_providers_have_no_extra_headers():
    provider = ProviderInfo(
        name="deepseek",
        display_name="DeepSeek",
        provider_type="deepseek",
        api_key="sk-test",
        base_url="https://api.deepseek.com/v1",
        is_default=False,
        enabled=True,
        extra={},
    )

    assert "extra_headers" not in build_litellm_params(provider)


def test_same_type_providers_keep_separate_api_keys():
    first = ProviderInfo(
        name="zhipu-work",
        display_name="Zhipu GLM",
        provider_type="zhipu",
        api_key="work-key",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        is_default=True,
        enabled=True,
        extra={},
    )
    second = ProviderInfo(
        name="zhipu-personal",
        display_name="Zhipu GLM",
        provider_type="zhipu",
        api_key="personal-key",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        is_default=False,
        enabled=True,
        extra={},
    )

    assert build_litellm_params(first)["api_key"] == "work-key"
    assert build_litellm_params(second)["api_key"] == "personal-key"


def test_resolve_model_for_provider_adds_chatgpt_prefix():
    provider = ProviderInfo(
        name="gptplus",
        display_name="ChatGPT",
        provider_type="chatgpt",
        api_key="oauth-managed",
        base_url="",
        is_default=False,
        enabled=True,
        extra={},
    )

    assert resolve_model_for_provider(provider, "gpt-5.4") == "chatgpt/gpt-5.4"


def test_resolve_model_for_provider_keeps_existing_prefix():
    provider = ProviderInfo(
        name="gptplus",
        display_name="ChatGPT",
        provider_type="chatgpt",
        api_key="oauth-managed",
        base_url="",
        is_default=False,
        enabled=True,
        extra={},
    )

    assert resolve_model_for_provider(provider, "chatgpt/gpt-5.4") == "chatgpt/gpt-5.4"


# --- Provider routing helpers + Gemini catalog entry (tasks P1 & P3) ---

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _gemini_provider(**overrides) -> ProviderInfo:
    fields = {
        "name": "gemini",
        "display_name": "Google Gemini",
        "provider_type": "gemini",
        "api_key": "g-key",
        "base_url": GEMINI_BASE_URL,
        "is_default": False,
        "enabled": True,
        "extra": {},
    }
    fields.update(overrides)
    return ProviderInfo(**fields)


def test_custom_llm_provider_for_gemini_is_gemini_and_chatgpt_is_none():
    assert provider_store.custom_llm_provider_for("gemini") == "gemini"
    assert provider_store.custom_llm_provider_for("chatgpt") is None


def test_custom_llm_provider_for_openai_compatible_types_defaults_to_openai():
    assert provider_store.custom_llm_provider_for("deepseek") == "openai"
    assert provider_store.custom_llm_provider_for("custom-x") == "openai"


def test_model_prefix_for_maps_provider_types_to_litellm_prefixes():
    assert provider_store.model_prefix_for("gemini") == "gemini"
    assert provider_store.model_prefix_for("chatgpt") == "chatgpt"
    assert provider_store.model_prefix_for("opencode-go") == "openai"


def test_resolve_model_for_provider_prefixes_bare_gemini_model():
    assert resolve_model_for_provider(_gemini_provider(), "gemini-2.5-pro") == "gemini/gemini-2.5-pro"


def test_resolve_model_for_provider_preserves_gemini_prefix():
    assert resolve_model_for_provider(_gemini_provider(), "gemini/gemini-2.5-pro") == "gemini/gemini-2.5-pro"


def test_build_litellm_params_for_gemini_routes_to_native_gemini_provider():
    params = build_litellm_params(_gemini_provider())

    assert params["custom_llm_provider"] == "gemini"
    assert params["api_base"] == GEMINI_BASE_URL
    assert params["api_key"] == "g-key"


def test_gemini_catalog_entry_declares_litellm_provider_and_static_models():
    assert "gemini" in provider_store.PROVIDER_CATALOG
    entry = provider_store.PROVIDER_CATALOG["gemini"]

    assert entry["litellm_provider"] == "gemini"
    assert entry["base_url"] == GEMINI_BASE_URL
    assert "gemini-2.5-pro" in entry["models"]
    assert len(entry["models"]) == 15


def test_static_models_for_returns_catalog_models():
    assert provider_store.static_models_for("gemini") == provider_store.GEMINI_MODELS
    assert provider_store.static_models_for("chatgpt") == provider_store.CHATGPT_MODELS
    assert provider_store.static_models_for("unknown-provider") == []



# ── OpenAI-compatible provider presets (groq/xai/mistral/openrouter/together/cerebras) ──

OPENAI_COMPATIBLE_PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "Groq"),
    "xai": ("https://api.x.ai/v1", "xAI (Grok)"),
    "mistral": ("https://api.mistral.ai/v1", "Mistral AI"),
    "openrouter": ("https://openrouter.ai/api/v1", "OpenRouter"),
    "together": ("https://api.together.xyz/v1", "Together AI"),
    "cerebras": ("https://api.cerebras.ai/v1", "Cerebras"),
}


def test_openai_compatible_presets_are_in_catalog_with_exact_base_url_and_name():
    for provider_type, (base_url, display_name) in OPENAI_COMPATIBLE_PRESETS.items():
        assert provider_type in provider_store.PROVIDER_CATALOG, provider_type
        entry = provider_store.PROVIDER_CATALOG[provider_type]
        assert entry["base_url"] == base_url, provider_type
        assert entry["display_name"] == display_name, provider_type


def test_openai_compatible_preset_routes_through_openai_provider():
    # Representative of all six presets: no litellm_provider override -> "openai".
    assert provider_store.custom_llm_provider_for("groq") == "openai"
    assert provider_store.model_prefix_for("groq") == "openai"


def test_resolve_model_for_provider_prefixes_bare_openai_compatible_model():
    groq = ProviderInfo(
        name="groq",
        display_name="Groq",
        provider_type="groq",
        api_key="gsk-test",
        base_url="https://api.groq.com/openai/v1",
        is_default=False,
        enabled=True,
        extra={},
    )

    assert resolve_model_for_provider(groq, "llama-3.3-70b") == "openai/llama-3.3-70b"


# ── Custom local OpenAI-compatible endpoint (llama.cpp, vLLM, LM Studio, ...) ──

LOCAL_OPENAI_COMPATIBLE_BASE_URL = "http://127.0.0.1:8080/v1"


def test_custom_catalog_entry_is_a_local_openai_compatible_endpoint():
    assert "custom" in provider_store.PROVIDER_CATALOG
    entry = provider_store.PROVIDER_CATALOG["custom"]

    assert entry.get("display_name") == "OpenAI-compatible (local / custom)"
    # No default URL: the user must type their own (e.g. llama-server's :8080/v1).
    assert entry.get("base_url") == ""
    # A purely local server needs no API key.
    assert entry.get("api_key_required") is False
    # Routes through litellm's openai provider, with no static model list.
    assert "litellm_provider" not in entry
    assert "models" not in entry


def test_custom_provider_type_routes_through_openai():
    assert provider_store.custom_llm_provider_for("custom") == "openai"
    assert provider_store.model_prefix_for("custom") == "openai"


def test_resolve_model_for_provider_prefixes_bare_custom_model():
    local = ProviderInfo(
        name="local-llama",
        display_name="llama.cpp (local)",
        provider_type="custom",
        api_key="",
        base_url=LOCAL_OPENAI_COMPATIBLE_BASE_URL,
        is_default=False,
        enabled=True,
        extra={},
    )

    assert resolve_model_for_provider(local, "qwen2.5-coder-7b") == "openai/qwen2.5-coder-7b"

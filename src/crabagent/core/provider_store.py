from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update

from crabagent.core.database import ProviderConfig, async_session_factory

logger = logging.getLogger(__name__)

# Process-lifetime fallback session ID for OpenCode Go requests made outside a
# conversation context (compress, title generation, mail matching, scheduler…).
# OpenCode Go rejects requests missing `x-opencode-session` since 2026-09; it
# prefers a stable ID per conversation for routing/prompt-cache optimisation.
_OPENCODE_FALLBACK_SESSION = str(uuid.uuid4())

try:
    from importlib.metadata import version as _pkg_version

    _CRABAGENT_USER_AGENT = f"crabagent/{_pkg_version('crabagent')}"
except Exception:  # pragma: no cover — package metadata unavailable (bare checkout)
    _CRABAGENT_USER_AGENT = "crabagent"


def opencode_extra_headers(session_id: str | None = None) -> dict[str, str]:
    """Headers OpenCode Go expects on every request.

    See https://opencode.ai/docs/go/#where-can-i-use-it — requests must carry
    a stable `x-opencode-session` (one ID per conversation) and identify
    themselves with their own user agent. Callers that know the conversation
    should pass its ID; otherwise a process-lifetime fallback is used.
    """
    return {
        "x-opencode-session": session_id or _OPENCODE_FALLBACK_SESSION,
        "User-Agent": _CRABAGENT_USER_AGENT,
    }


def _get_fernet():
    from cryptography.fernet import Fernet

    from crabagent.core.config import settings

    return Fernet(settings.get_encryption_key().encode())


def encrypt_api_key(api_key: str) -> str:
    return _get_fernet().encrypt(api_key.encode()).decode()


def decrypt_api_key(encrypted: str) -> str:
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except Exception:
        return encrypted


def _is_encrypted(value: str) -> bool:
    try:
        _get_fernet().decrypt(value.encode())
        return True
    except Exception:
        return False


@dataclass
class ProviderInfo:
    name: str
    display_name: str
    provider_type: str
    api_key: str
    base_url: str
    is_default: bool
    enabled: bool
    extra: dict[str, Any]


CHATGPT_MODELS = [
    # Current generation (verified on Plus 2026-09 via GET /codex/models)
    # GPT-6 family reports max_context_window = 872_000.
    # Mirror every *visible* slug returned by GET /codex/models here, in its
    # reported priority order: this list is the fallback shown whenever the live
    # call fails (a startup network blip is enough), so a slug missing here is
    # invisible to the user until the live list comes back.
    "gpt-6.1-sol",
    "gpt-6-astra",
    "gpt-6.1-terra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-reserve",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    # Pro-only models (require ChatGPT Pro subscription)
    "gpt-5.5-pro",
    "gpt-5.4-pro",
    # Legacy models (may still work on older accounts)
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.3-instant",
    "gpt-5.3-chat-latest",
    "gpt-5.2-codex",
    "gpt-5.2",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
]

CHATGPT_IMAGE_MODELS = [
    "gpt-image-2.6-sunburst",
    "gpt-image-2.6-flare",
    "image-2.5",
    "image-2",
    "gpt-image-2",
    "chatgpt-image-latest",
]

GEMINI_MODELS = [
    "gemini-4-argon",
    "gemini-3.1-pro-preview",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-pro-latest",
]


PROVIDER_CATALOG: dict[str, dict] = {
    "chatgpt": {
        "base_url": "",
        "display_name": "ChatGPT Subscription (Plus/Pro)",
        "auth_type": "oauth",
        "models": CHATGPT_MODELS,
    },
    "opencode-go": {"base_url": "https://opencode.ai/zen/go/v1", "display_name": "OpenCode Go"},
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "display_name": "Google Gemini",
        "litellm_provider": "gemini",
        "models": GEMINI_MODELS,
    },
    "openai": {"base_url": "https://api.openai.com/v1", "display_name": "OpenAI"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "display_name": "Anthropic"},
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "display_name": "Zhipu GLM",
        "variants": [
            {"id": "standard", "display_name": "Standard", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
            {"id": "coding", "display_name": "Coding Plan", "base_url": "https://open.bigmodel.cn/api/coding/paas/v4"},
        ],
    },
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "display_name": "DeepSeek"},
    "minimax": {
        "base_url": "https://api.minimax.chat/v1",
        "display_name": "MiniMax",
        "variants": [
            {"id": "cn", "display_name": "China", "base_url": "https://api.minimax.chat/v1"},
            {"id": "intl", "display_name": "Global", "base_url": "https://api.minimaxi.com/v1"},
        ],
    },
    "volcengine": {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "display_name": "Volcengine Ark"},
    "bailian": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "display_name": "Alibaba Bailian"},
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "display_name": "Kimi (Moonshot AI)",
        "variants": [
            {"id": "standard", "display_name": "Moonshot", "base_url": "https://api.moonshot.cn/v1"},
            {"id": "coding", "display_name": "Kimi Code Plan", "base_url": "https://api.kimi.com/coding/v1"},
        ],
    },
    "stepfun": {
        "base_url": "https://api.stepfun.com/v1",
        "display_name": "StepFun",
        "variants": [
            {"id": "cn", "display_name": "China", "base_url": "https://api.stepfun.com/v1"},
            {"id": "global", "display_name": "Global", "base_url": "https://api.stepfun.ai/v1"},
            {
                "id": "plan_cn",
                "display_name": "Step Plan Subscription (China)",
                "base_url": "https://api.stepfun.com/step_plan/v1",
            },
            {
                "id": "plan_global",
                "display_name": "Step Plan Subscription (Global)",
                "base_url": "https://api.stepfun.ai/step_plan/v1",
            },
        ],
    },
    "groq": {"base_url": "https://api.groq.com/openai/v1", "display_name": "Groq"},
    "xai": {"base_url": "https://api.x.ai/v1", "display_name": "xAI (Grok)"},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "display_name": "Mistral AI"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "display_name": "OpenRouter"},
    "together": {"base_url": "https://api.together.xyz/v1", "display_name": "Together AI"},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "display_name": "Cerebras"},
    # Local or self-hosted OpenAI-compatible server (llama.cpp, vLLM, LM Studio, ...).
    # No base_url on purpose: the user types their own, and the model list is
    # fetched live from {base_url}/models.
    "custom": {
        "base_url": "",
        "display_name": "OpenAI-compatible (local / custom)",
        "api_key_required": False,
    },
}


def resolve_catalog_variant(provider_type: str, variant_id: str | None) -> str | None:
    entry = PROVIDER_CATALOG.get(provider_type)
    if not entry or not variant_id:
        return None
    for v in entry.get("variants", []):
        if v["id"] == variant_id:
            return v["base_url"]
    return None


def custom_llm_provider_for(provider_type: str) -> str | None:
    if provider_type == "chatgpt":
        return None
    entry = PROVIDER_CATALOG.get(provider_type, {})
    return entry.get("litellm_provider", "openai")


def model_prefix_for(provider_type: str) -> str:
    if provider_type == "chatgpt":
        return "chatgpt"
    return custom_llm_provider_for(provider_type) or "openai"


def static_models_for(provider_type: str) -> list[str]:
    entry = PROVIDER_CATALOG.get(provider_type, {})
    return list(entry.get("models", []))


def _to_info(row: ProviderConfig) -> ProviderInfo:
    try:
        extra = json.loads(row.extra) if row.extra else {}
    except json.JSONDecodeError:
        extra = {}
    return ProviderInfo(
        name=row.name,
        display_name=row.display_name,
        provider_type=row.provider_type,
        api_key=decrypt_api_key(row.api_key),
        base_url=row.base_url,
        is_default=row.is_default,
        enabled=row.enabled,
        extra=extra,
    )


def _no_default_rows() -> int:
    return (
        update(ProviderConfig)
        .where(ProviderConfig.is_default == True)  # noqa: E712
        .values(is_default=False)
    )


async def get_default_provider() -> ProviderInfo | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(ProviderConfig).where(
                ProviderConfig.is_default == True,  # noqa: E712
                ProviderConfig.enabled == True,  # noqa: E712
            )
        )
        row = result.scalar_one_or_none()
        return _to_info(row) if row else None


async def get_provider(name: str) -> ProviderInfo | None:
    async with async_session_factory() as session:
        result = await session.execute(select(ProviderConfig).where(ProviderConfig.name == name))
        row = result.scalar_one_or_none()
        return _to_info(row) if row else None


async def list_providers() -> list[ProviderInfo]:
    async with async_session_factory() as session:
        result = await session.execute(select(ProviderConfig).order_by(ProviderConfig.id))
        return [_to_info(row) for row in result.scalars().all()]


async def create_provider(
    name: str,
    provider_type: str,
    api_key: str,
    display_name: str = "",
    base_url: str = "",
    is_default: bool = False,
    extra: dict[str, Any] | None = None,
) -> ProviderInfo:
    async with async_session_factory() as session:
        if is_default:
            await session.execute(_no_default_rows())
        cfg = ProviderConfig(
            name=name,
            display_name=display_name or name,
            provider_type=provider_type,
            api_key=encrypt_api_key(api_key),
            base_url=base_url,
            is_default=is_default,
            extra=json.dumps(extra or {}),
        )
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
        return _to_info(cfg)


async def set_default_provider(name: str) -> bool:
    async with async_session_factory() as session:
        result = await session.execute(select(ProviderConfig).where(ProviderConfig.name == name))
        row = result.scalar_one_or_none()
        if not row:
            return False
        await session.execute(_no_default_rows())
        await session.execute(update(ProviderConfig).where(ProviderConfig.name == name).values(is_default=True))
        await session.commit()
        return True


async def update_provider(name: str, **kwargs) -> ProviderInfo | None:
    async with async_session_factory() as session:
        result = await session.execute(select(ProviderConfig).where(ProviderConfig.name == name))
        row = result.scalar_one_or_none()
        if not row:
            return None
        if kwargs.get("is_default"):
            await session.execute(_no_default_rows())
        for key, value in kwargs.items():
            if value is not None and hasattr(row, key):
                if key == "api_key":
                    value = encrypt_api_key(value)
                setattr(row, key, value)
        await session.commit()
        await session.refresh(row)
        return _to_info(row)


async def migrate_plaintext_keys():
    async with async_session_factory() as session:
        result = await session.execute(select(ProviderConfig))
        rows = result.scalars().all()
        migrated = 0
        for row in rows:
            if not _is_encrypted(row.api_key):
                row.api_key = encrypt_api_key(row.api_key)
                migrated += 1
        if migrated:
            await session.commit()
            logger.info(f"Migrated {migrated} provider API keys to encrypted storage")


async def delete_provider(name: str) -> bool:
    async with async_session_factory() as session:
        result = await session.execute(select(ProviderConfig).where(ProviderConfig.name == name))
        row = result.scalar_one_or_none()
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True


def build_litellm_params(provider: ProviderInfo, proxy: str = "", session_id: str | None = None) -> dict[str, Any]:
    """Build litellm connection params for a provider.

    ``session_id`` is only used by the OpenCode Go provider, which requires a
    stable per-conversation `x-opencode-session` header.
    """
    if provider.provider_type == "chatgpt":
        params: dict[str, Any] = {}
    else:
        params = {
            "api_key": provider.api_key,
            "custom_llm_provider": custom_llm_provider_for(provider.provider_type) or "openai",
        }
        if provider.base_url:
            params["api_base"] = provider.base_url
        if provider.provider_type == "opencode-go":
            params["extra_headers"] = opencode_extra_headers(session_id)
    if proxy:
        params["proxy"] = proxy
    return params


async def resolve_litellm_params(provider: ProviderInfo, session_id: str | None = None) -> dict[str, Any]:
    from crabagent.core.proxy import resolve_llm_proxy

    proxy = await resolve_llm_proxy(provider)
    return build_litellm_params(provider, proxy, session_id=session_id)


def resolve_model_for_provider(provider: ProviderInfo, model: str) -> str:
    if "/" in model:
        return model
    return f"{model_prefix_for(provider.provider_type)}/{model}"


async def fetch_models(provider_name: str) -> list[str]:
    p = await get_provider(provider_name)
    if not p:
        return []
    base = p.base_url.rstrip("/")
    try:
        import httpx

        from crabagent.core.proxy import resolve_llm_proxy

        proxy = await resolve_llm_proxy(p)
        client_kwargs = {"timeout": 15.0}
        if proxy:
            client_kwargs["proxy"] = proxy
        headers = {"Authorization": f"Bearer {p.api_key}"}
        if p.provider_type == "opencode-go":
            headers.update(opencode_extra_headers())
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(
                f"{base}/models",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception:
        logger.warning("Failed to fetch models for provider %s", provider_name, exc_info=True)
        return []

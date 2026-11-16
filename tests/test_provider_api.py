from __future__ import annotations

import crabagent.serve.api.provider as provider_api
from crabagent.core.provider_store import GEMINI_MODELS, ProviderInfo


def _gemini_provider(extra: dict) -> ProviderInfo:
    return ProviderInfo(
        name="g",
        display_name="Google Gemini",
        provider_type="gemini",
        api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        is_default=False,
        enabled=True,
        extra=extra,
    )


async def test_catalog_has_gemini_static_models():
    entries = {e.type: e for e in await provider_api.get_catalog(user=None)}
    entry = entries["gemini"]
    assert entry.models == GEMINI_MODELS
    assert "litellm_provider" not in entry.model_dump()


async def test_gemini_models_are_static_plus_extra_models(monkeypatch):
    info = _gemini_provider({"extra_models": ["my-gemini-alias"]})

    async def fake_get_provider(name):
        return info

    monkeypatch.setattr(provider_api, "get_provider", fake_get_provider)

    models = await provider_api.get_provider_models("g", user=None)

    assert models == [
        *({"id": m, "owned_by": "gemini"} for m in GEMINI_MODELS),
        {"id": "my-gemini-alias", "owned_by": "custom"},
    ]
    assert len(models) == len(GEMINI_MODELS) + 1


async def test_gemini_models_do_not_hit_the_network(monkeypatch):
    info = _gemini_provider({})

    async def fake_get_provider(name):
        return info

    def _no_network(*args, **kwargs):
        raise AssertionError("gemini must not use the network")

    monkeypatch.setattr(provider_api, "get_provider", fake_get_provider)
    monkeypatch.setattr(provider_api.httpx, "AsyncClient", _no_network)

    models = await provider_api.get_provider_models("g", user=None)

    assert [m["id"] for m in models] == GEMINI_MODELS

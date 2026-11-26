from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import crabagent.core.provider_store as provider_store
import crabagent.serve.api.provider as provider_api
from crabagent.core import database as db
from crabagent.core.provider_store import GEMINI_MODELS, ProviderInfo, build_litellm_params
from crabagent.serve.deps import get_current_user


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


# --- Custom local OpenAI-compatible endpoint (llama.cpp / llama-server, ...) ---

LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    """App with the providers router on a throwaway database."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'providers.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # provider_store and provider.py each import the factory at module level.
    monkeypatch.setattr(provider_store, "async_session_factory", factory)
    monkeypatch.setattr(provider_api, "async_session_factory", factory)

    app = FastAPI()
    app.include_router(provider_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: db.User(id=1, username="tester")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def test_catalog_endpoint_exposes_the_custom_local_entry(client):
    response = await client.get("/api/providers/catalog")
    assert response.status_code == 200, response.text
    entries = {e["type"]: e for e in response.json()}

    assert "custom" in entries
    entry = entries["custom"]
    assert entry["base_url"] == ""
    assert entry["models"] == []


async def test_create_custom_provider_with_empty_api_key(client):
    response = await client.post(
        "/api/providers",
        json={
            "name": "local-llama",
            "type": "custom",
            "api_key": "",
            "display_name": "llama.cpp (local)",
            "base_url": LOCAL_BASE_URL,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["type"] == "custom"
    assert body["base_url"] == LOCAL_BASE_URL

    stored = await provider_store.get_provider("local-llama")
    assert stored is not None
    assert stored.base_url == LOCAL_BASE_URL

    # The local endpoint must reach litellm as an openai provider, with the
    # user's URL and a non-empty key (litellm rejects an empty api_key).
    params = build_litellm_params(stored)
    assert params["custom_llm_provider"] == "openai"
    assert params["api_base"] == LOCAL_BASE_URL
    assert params["api_key"]


async def test_cloud_provider_still_rejects_an_empty_api_key(client):
    for provider_type, name in (("openai", "cloud-openai"), ("groq", "cloud-groq")):
        response = await client.post(
            "/api/providers",
            json={"name": name, "type": provider_type, "api_key": ""},
        )

        assert response.status_code == 400, (provider_type, response.text)
        assert await provider_store.get_provider(name) is None

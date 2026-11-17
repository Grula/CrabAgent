import json
from types import SimpleNamespace

import pytest

from crabagent.core.agent.tools import collaboration_browser as browser


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["authorized", "blocked"])
async def test_local_preview_permission_is_owned_by_electron(monkeypatch, status):
    calls = []

    def request(command, payload=None, *, context):
        calls.append((command, payload))
        return {"status": status, "origin": "http://127.0.0.1:8766"}

    async def audit(*args, **kwargs):
        pass

    monkeypatch.setattr(browser, "_bridge_request", request)
    monkeypatch.setattr(browser, "record_browser_event", audit)
    ctx = SimpleNamespace(metadata={})
    result = json.loads(await browser.collab_browser_authorize_local_preview("http://127.0.0.1:8766/a", ctx))
    assert result["status"] == status
    assert calls == [
        ("status", None),
        ("authorize_local_preview", {"url": "http://127.0.0.1:8766/a"}),
    ]


@pytest.mark.asyncio
async def test_authorize_all_ports_includes_flag(monkeypatch):
    calls = []

    def request(command, payload=None, *, context):
        calls.append((command, payload))
        return {"status": "authorized", "origin": "http://localhost:5173"}

    async def audit(*args, **kwargs):
        pass

    monkeypatch.setattr(browser, "_bridge_request", request)
    monkeypatch.setattr(browser, "record_browser_event", audit)
    ctx = SimpleNamespace(metadata={})
    await browser.collab_browser_authorize_local_preview("http://localhost", all_ports=True, context=ctx)
    assert calls == [
        ("status", None),
        ("authorize_local_preview", {"url": "http://localhost", "all_ports": True}),
    ]

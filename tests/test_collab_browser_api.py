"""Web viewer API for the collaboration browser (serve/api/collab_browser.py)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from crabagent.core.computer.bridge_service import BrowserBridgeService
from crabagent.serve.api import collab_browser as api


class _Bridge:
    """Stand-in for _post_command: replays scripted (status, body) replies."""

    def __init__(self, replies: dict[str, tuple[int, dict[str, Any]]]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, session, url, token, command, payload):
        self.calls.append((command, payload))
        return self.replies.get(command, (200, {"result": {}}))


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CRAB_COLLAB_BROWSER_PORT", "8765")
    monkeypatch.setenv("CRAB_COLLAB_BROWSER_TOKEN", "test-token")

    def install(replies: dict[str, tuple[int, dict[str, Any]]] | None = None) -> _Bridge:
        fake = _Bridge(replies or {})
        monkeypatch.setattr(api, "_post_command", fake)
        return fake

    return install


async def test_typed_localhost_is_authorized_then_opened_over_http(bridge):
    fake = bridge({"navigate": (200, {"result": {"url": "http://localhost:5173/processamento"}})})

    data = await api.collab_browser_navigate(api.NavigateBody(url="localhost:5173/processamento"), user=None)

    assert data["result"]["url"] == "http://localhost:5173/processamento"
    assert [c for c, _ in fake.calls] == ["authorize_local_preview", "navigate"]
    assert fake.calls[0][1] == {"url": "http://localhost:5173/processamento"}
    assert fake.calls[1][1]["url"] == "http://localhost:5173/processamento"


async def test_public_site_is_not_authorized(bridge):
    fake = bridge()

    await api.collab_browser_navigate(api.NavigateBody(url="https://example.com"), user=None)

    assert [c for c, _ in fake.calls] == ["navigate"]


async def test_bare_public_host_keeps_bridge_default(bridge):
    fake = bridge()

    await api.collab_browser_navigate(api.NavigateBody(url="example.com"), user=None)

    assert fake.calls[0][1]["url"] == "example.com"


async def test_reserved_port_reports_reason_and_does_not_navigate(bridge):
    reason = "LOCAL_PREVIEW_RESERVED: CrabAgent service ports cannot be authorized"
    fake = bridge({"authorize_local_preview": (400, {"detail": reason})})

    with pytest.raises(HTTPException) as exc:
        await api.collab_browser_navigate(api.NavigateBody(url="http://localhost:5210"), user=None)

    assert exc.value.status_code == 400
    assert exc.value.detail == reason
    assert [c for c, _ in fake.calls] == ["authorize_local_preview"]


async def test_refused_navigation_passes_reason_through(bridge):
    reason = "NAVIGATION_FAILED: net::ERR_CONNECTION_REFUSED at http://localhost:5173/"
    bridge({"navigate": (400, {"detail": reason})})

    with pytest.raises(HTTPException) as exc:
        await api.collab_browser_navigate(api.NavigateBody(url="http://localhost:5173"), user=None)

    assert exc.value.status_code == 400
    assert exc.value.detail == reason


async def test_bridge_auth_failure_is_reported_as_unavailable(bridge):
    bridge({"navigate": (401, {"detail": "Unauthorized"})})

    with pytest.raises(HTTPException) as exc:
        await api.collab_browser_navigate(api.NavigateBody(url="https://example.com"), user=None)

    assert exc.value.status_code == 503


@pytest.mark.parametrize(("bridge_status", "expected"), [(500, 502), (503, 503), (401, 503)])
async def test_frame_failure_separates_busy_from_down(monkeypatch: pytest.MonkeyPatch, bridge_status, expected):
    from aiohttp import web

    async def frame(request):
        return web.json_response({"detail": "x"}, status=bridge_status)

    app = web.Application()
    app.router.add_get("/frame", frame)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("CRAB_COLLAB_BROWSER_PORT", str(port))
    monkeypatch.setenv("CRAB_COLLAB_BROWSER_TOKEN", "test-token")
    try:
        with pytest.raises(HTTPException) as exc:
            await api.get_collab_browser_frame(user=None)
        assert exc.value.status_code == expected
    finally:
        await runner.cleanup()


def test_crabagent_port_is_reserved_without_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CRAB_APP_PORT", raising=False)
    service = BrowserBridgeService(headless=True)

    with pytest.raises(ValueError, match="LOCAL_PREVIEW_RESERVED"):
        service.local_preview_policy.grant("http://localhost:5210")


def test_crabagent_port_is_never_allowed_by_wildcard_grant(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CRAB_APP_PORT", raising=False)
    service = BrowserBridgeService(headless=True)
    service.local_preview_policy.grant("http://localhost", all_ports=True)

    assert service.local_preview_policy.allows("http://localhost:5173")
    assert not service.local_preview_policy.allows("http://localhost:5210")


async def test_pause_and_resume_are_forwarded(bridge):
    fake = bridge()

    assert await api.collab_browser_pause(api.PauseBody(paused=True), user=None) == {"paused": True}
    assert await api.collab_browser_pause(api.PauseBody(paused=False), user=None) == {"paused": False}

    assert [c for c, _ in fake.calls] == ["stop", "resume"]


async def test_status_reports_paused(bridge):
    bridge({"status": (200, {"result": {"paused": True, "url": "about:blank"}})})

    assert await api.collab_browser_status(user=None) == {"paused": True}


async def test_viewer_commands_speak_for_the_person():
    from crabagent.core.computer.bridge_service import USER_TASK_ID

    assert api._envelope("navigate", {})["task_id"] == USER_TASK_ID

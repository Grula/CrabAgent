"""P4: persistent loopback-preview allow via CRAB_ALLOW_LOOPBACK_PREVIEWS."""

from crabagent.core.computer.bridge_service import BrowserBridgeService
from crabagent.core.computer.url_policy import LocalPreviewPolicy


def test_allow_all_loopback_allows_any_loopback_port():
    policy = LocalPreviewPolicy(allow_all_loopback=True)

    assert policy.allows("http://localhost:5173")
    assert policy.allows("http://127.0.0.1:8080")


def test_allow_all_loopback_still_excludes_forbidden_ports():
    policy = LocalPreviewPolicy(allow_all_loopback=True, forbidden_ports=lambda: [5173])

    assert not policy.allows("http://localhost:5173")
    assert policy.allows("http://localhost:3000")


def test_allow_all_loopback_rejects_non_loopback():
    policy = LocalPreviewPolicy(allow_all_loopback=True)

    assert not policy.allows("http://192.168.1.5")
    assert not policy.allows("https://example.com")


def test_allow_all_loopback_defaults_off():
    policy = LocalPreviewPolicy()

    assert not policy.allows("http://localhost:5173")


def test_bridge_honors_allow_loopback_env(monkeypatch):
    monkeypatch.setenv("CRAB_ALLOW_LOOPBACK_PREVIEWS", "1")

    service = BrowserBridgeService(headless=True)

    assert service.local_preview_policy.allows("http://localhost:5173")


def test_bridge_defaults_loopback_off(monkeypatch):
    monkeypatch.delenv("CRAB_ALLOW_LOOPBACK_PREVIEWS", raising=False)

    service = BrowserBridgeService(headless=True)

    assert not service.local_preview_policy.allows("http://localhost:5173")

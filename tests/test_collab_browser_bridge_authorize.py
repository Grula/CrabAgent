from crabagent.core.computer.bridge_service import BrowserBridgeService


def test_authorize_local_preview_all_ports():
    svc = BrowserBridgeService(headless=True)
    svc._authorize_local_preview({"url": "http://localhost", "all_ports": True})
    assert svc.local_preview_policy.allows("http://localhost:5173") is True

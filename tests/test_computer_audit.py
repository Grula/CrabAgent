from types import SimpleNamespace

import pytest

from crabagent.core.computer import audit


def test_origin_drops_credentials_path_and_query():
    assert audit.safe_origin("https://user:secret@example.com/path?token=secret") == "https://example.com"
    assert audit.safe_origin("file:///tmp/private") == ""


@pytest.mark.asyncio
async def test_audit_without_bound_session_does_not_touch_database(monkeypatch):
    def fail():
        pytest.fail("audit without context must not connect to DB")

    monkeypatch.setattr(audit, "async_session_factory", fail)
    await audit.record_browser_event(SimpleNamespace(metadata={}), "action_result", action="click_element")

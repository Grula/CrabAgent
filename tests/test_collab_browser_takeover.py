from __future__ import annotations

import io
import json
from typing import Any
from urllib.error import HTTPError

import pytest

from crabagent.core.agent.tools import collaboration_browser as browser

BRIDGE_URL = "http://127.0.0.1:8765/"
TASK_MISMATCH = "TASK_MISMATCH: stop and resume before switching browser tasks"
STOPPED = "STOPPED: browser actions are paused"


class _FakeResponse:
    """Minimal stand-in for the object returned by urllib's urlopen."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _Bridge:
    """Fake urlopen that replays scripted outcomes and records every command."""

    def __init__(self, outcomes: list[bytes | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.commands: list[str] = []
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, request: Any, timeout: Any = None) -> _FakeResponse:
        envelope = json.loads(request.data.decode("utf-8"))
        self.commands.append(envelope["command"])
        self.payloads.append(envelope.get("payload") or {})
        if not self._outcomes:
            raise AssertionError(f"unexpected extra bridge request for {envelope['command']!r}")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)


def _ok(result: dict[str, Any]) -> bytes:
    return json.dumps({"result": result}).encode("utf-8")


def _http_error(detail: str, code: int = 409) -> HTTPError:
    body = json.dumps({"detail": detail}).encode("utf-8")
    return HTTPError(BRIDGE_URL, code, "Conflict", {}, io.BytesIO(body))


@pytest.fixture(autouse=True)
def _bridge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRAB_COLLAB_BROWSER_PORT", "8765")
    monkeypatch.setenv("CRAB_COLLAB_BROWSER_TOKEN", "test-token")


def test_task_mismatch_does_not_take_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _Bridge([_http_error(TASK_MISMATCH)])
    monkeypatch.setattr(browser, "urlopen", bridge)

    with pytest.raises(RuntimeError, match="TASK_MISMATCH: another agent task") as exc:
        browser._bridge_request("navigate", {"url": "https://example.test"})

    assert bridge.commands == ["navigate"]
    assert "Do not retry" in str(exc.value)
    assert browser._is_session_state_error(exc.value)


def test_user_pause_is_not_undone(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _Bridge([_http_error(STOPPED)])
    monkeypatch.setattr(browser, "urlopen", bridge)

    with pytest.raises(RuntimeError, match="STOPPED: the user paused") as exc:
        browser._bridge_request("observe")

    assert bridge.commands == ["observe"]
    assert browser._is_session_state_error(exc.value)


def test_no_resume_for_non_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _Bridge([_http_error("PRIVATE_NETWORK_BLOCKED: localhost is not allowed")])
    monkeypatch.setattr(browser, "urlopen", bridge)

    with pytest.raises(RuntimeError, match="PRIVATE_NETWORK_BLOCKED"):
        browser._bridge_request("observe")

    assert bridge.commands == ["observe"]


@pytest.mark.parametrize("command", ["back", "forward", "reload"])
def test_history_commands_carry_an_action_id(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    bridge = _Bridge([_ok({"url": "https://example.test/"})])
    monkeypatch.setattr(browser, "urlopen", bridge)

    browser._bridge_request(command)

    assert bridge.commands == [command]
    assert "action_id" in bridge.payloads[0]

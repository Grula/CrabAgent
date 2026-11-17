"""Who may drive the collaboration browser: agent tasks vs. the person at the viewer."""

from __future__ import annotations

import uuid

import pytest

from crabagent.core.computer.bridge_service import USER_TASK_ID, BridgeError, BrowserBridgeService


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> BrowserBridgeService:
    service = BrowserBridgeService(headless=True)

    async def handled(command, payload, *, as_user=False):
        return {"command": command, "as_user": as_user}

    monkeypatch.setattr(service, "_handle_command", handled)
    return service


def _action() -> dict[str, str]:
    return {"action_id": str(uuid.uuid4())}


async def test_second_agent_task_is_refused(bridge):
    agent_a, agent_b = str(uuid.uuid4()), str(uuid.uuid4())
    await bridge._dispatch("navigate", _action(), agent_a)

    with pytest.raises(BridgeError, match="TASK_MISMATCH: another task"):
        await bridge._dispatch("navigate", _action(), agent_b)
    assert bridge.task_id == agent_a


async def test_viewer_drives_without_taking_the_browser(bridge):
    agent = str(uuid.uuid4())
    await bridge._dispatch("navigate", _action(), agent)

    result = await bridge._dispatch("back", _action(), USER_TASK_ID)

    assert result == {"command": "back", "as_user": True}
    assert bridge.task_id == agent  # the agent still holds it


async def test_viewer_never_binds_an_idle_browser(bridge):
    await bridge._dispatch("navigate", _action(), USER_TASK_ID)

    assert bridge.task_id is None
    await bridge._dispatch("navigate", _action(), str(uuid.uuid4()))  # an agent can still take it


async def test_viewer_is_limited_to_its_own_controls(bridge):
    for command in ("click", "type", "release", "observe"):
        with pytest.raises(BridgeError, match="viewer"):
            await bridge._dispatch(command, _action(), USER_TASK_ID)


async def test_viewer_pauses_and_resumes_the_agents(monkeypatch: pytest.MonkeyPatch):
    service = BrowserBridgeService(headless=True)
    agent = str(uuid.uuid4())

    await service._dispatch("stop", {}, USER_TASK_ID)
    with pytest.raises(BridgeError, match="STOPPED"):
        await service._dispatch("click", _action(), agent)

    await service._dispatch("resume", {}, USER_TASK_ID)
    assert not service.stopped


async def test_pause_holds_agents_but_not_the_person(monkeypatch: pytest.MonkeyPatch):
    service = BrowserBridgeService(headless=True)

    async def navigate(payload):
        return {"navigated": True}

    monkeypatch.setattr(service, "_navigate", navigate)
    await service._dispatch("stop", {}, str(uuid.uuid4()))

    with pytest.raises(BridgeError, match="STOPPED"):
        await service._dispatch("navigate", _action(), str(uuid.uuid4()))
    assert await service._dispatch("navigate", _action(), USER_TASK_ID) == {"navigated": True}
    assert service.stopped  # the person's navigation did not lift the pause


# --- Delegation hands the browser to the sub-agent ------------------------------


def _delegation_harness(monkeypatch: pytest.MonkeyPatch):
    from pathlib import Path

    from crabagent.core.agent import agents as agents_module
    from crabagent.core.agent.context import AgentContext
    from crabagent.core.agent.tools import collaboration_browser

    agent_def = {"name": "implementer", "display_name": "Dev", "role": "r", "goal": "g", "backstory": "b", "tool_permissions": {}}

    async def returns(value):
        return value

    monkeypatch.setattr(agents_module, "get_agent", lambda *a, **k: returns(agent_def))
    monkeypatch.setattr(agents_module, "_load_shared_context", lambda *a, **k: returns(""))
    monkeypatch.setattr(agents_module, "load_sub_agent_model_map", lambda *a, **k: returns({}))

    order: list[str] = []

    def bridge_request(command, payload=None, *, context=None):
        order.append(f"{command}:{context.metadata.get('_run_id')}")
        return {}

    async def run_agent(sub_context, task, *args, **kwargs):
        order.append("sub-agent runs")
        sub_context.messages.append({"role": "assistant", "content": "done"})
        return sub_context.messages

    monkeypatch.setattr(collaboration_browser, "_bridge_request", bridge_request)
    monkeypatch.setattr(agents_module, "run_agent", run_agent, raising=False)
    monkeypatch.setattr("crabagent.core.agent.loop.run_agent", run_agent)
    agents_module._running_sub_agents.clear()
    parent = AgentContext(workspace=Path("/tmp"), metadata={"user_id": 0, "_run_id": "parent-run"})

    async def delegate():
        await agents_module.spawn_sub_agent(parent, agent_name="implementer", task="x", agent_def=agent_def)

    return parent, order, delegate


async def test_parent_hands_the_browser_to_its_sub_agent(monkeypatch: pytest.MonkeyPatch):
    parent, order, delegate = _delegation_harness(monkeypatch)
    parent.metadata["_collab_browser_used"] = True

    await delegate()

    assert order == ["release:parent-run", "sub-agent runs"]
    assert "_collab_browser_used" not in parent.metadata


async def test_parent_that_never_used_the_browser_releases_nothing(monkeypatch: pytest.MonkeyPatch):
    _, order, delegate = _delegation_harness(monkeypatch)

    await delegate()

    assert order == ["sub-agent runs"]


# --- Every page load is checked, not just typed addresses -----------------------


class _Request:
    def __init__(self, url: str, navigation: bool = True) -> None:
        self.url = url
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class _Route:
    def __init__(self) -> None:
        self.outcome: str | None = None

    async def continue_(self) -> None:
        self.outcome = "continued"

    async def fulfill(self, status: int = 200, **kwargs) -> None:
        self.outcome = f"blocked page ({status})"


@pytest.mark.parametrize(
    ("url", "navigation", "outcome"),
    [
        ("http://127.0.0.1:5173/", True, "blocked page (403)"),  # e.g. back to a page whose grant expired
        ("http://192.168.1.1/admin", True, "blocked page (403)"),  # e.g. a redirect or link into the LAN
        ("http://93.184.215.14/", True, "continued"),  # public address
        ("http://127.0.0.1:5173/app.js", False, "continued"),  # subresources are not checked
    ],
)
async def test_page_loads_follow_the_address_rules(url, navigation, outcome):
    service = BrowserBridgeService(headless=True)
    route = _Route()

    await service._guard_navigation(route, _Request(url, navigation))

    assert route.outcome == outcome


async def test_granted_local_preview_loads():
    service = BrowserBridgeService(headless=True)
    service.local_preview_policy.grant("http://127.0.0.1:5173")
    route = _Route()

    await service._guard_navigation(route, _Request("http://127.0.0.1:5173/page"))

    assert route.outcome == "continued"


class _Page:
    """A page that has already landed somewhere (e.g. through a redirect)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.content: str | None = None

    async def set_content(self, html: str) -> None:
        self.content = html


async def test_redirect_into_private_address_is_hidden_before_anyone_reads_it(monkeypatch: pytest.MonkeyPatch):
    service = BrowserBridgeService(headless=True)
    service._page = _Page("http://127.0.0.1:5197/secret")

    async def ready():
        return None

    async def observe():
        raise AssertionError("the blocked page must not be observed")

    monkeypatch.setattr(service, "_ensure_browser", ready)
    monkeypatch.setattr(service, "_observe", observe)

    with pytest.raises(BridgeError, match="^PRIVATE_NETWORK_BLOCKED"):
        await service._dispatch("observe", {}, str(uuid.uuid4()))
    assert "blocked" in service._page.content


async def test_allowed_page_is_left_alone():
    service = BrowserBridgeService(headless=True)
    service.local_preview_policy.grant("http://127.0.0.1:5173")
    service._page = _Page("http://127.0.0.1:5173/")

    await service._enforce_page_policy()

    assert service._page.content is None


# --- Agent history tool -----------------------------------------------------------


async def test_agent_history_tool_only_takes_browser_buttons():
    from crabagent.core.agent.tools import collaboration_browser

    with pytest.raises(ValueError):
        await collaboration_browser.collab_browser_history("close")

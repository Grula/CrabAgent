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
    for command in ("click", "type", "resume", "stop", "release", "observe"):
        with pytest.raises(BridgeError, match="viewer"):
            await bridge._dispatch(command, _action(), USER_TASK_ID)


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

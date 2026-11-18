from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import pytest

from crabagent.serve.api import prompt as prompt_api


def test_save_image_temp_writes_file_and_returns_metadata(tmp_path: Path, monkeypatch):
    payload = base64.b64encode(b"png-bytes").decode()
    data_url = f"data:image/png;base64,{payload}"

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    result = prompt_api._save_image_temp(data_url)

    assert result["mime"] == "image/png"
    assert result["size_kb"] >= 0
    assert Path(result["file_path"]).exists()
    assert Path(result["file_path"]).read_bytes() == b"png-bytes"


def test_extract_local_images_ignores_missing_and_non_image_files(tmp_path: Path):
    text_file = tmp_path / "note.txt"
    text_file.write_text("hello", encoding="utf-8")
    image_file = tmp_path / "a.png"
    image_file.write_bytes(b"fake-image")

    message = f"see {image_file} and {text_file} and /no/such/file.jpg"
    blocks = prompt_api._extract_local_images(message)

    assert len(blocks) == 1
    assert blocks[0]["type"] == "image_url"
    assert blocks[0]["file_path"] == str(image_file)
    assert blocks[0]["mime"] == "image/png"
    assert blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_extract_local_images_handles_parenthesized_paths(tmp_path: Path):
    image_file = tmp_path / "b.jpg"
    image_file.write_bytes(b"jpg-bytes")

    message = f"please inspect ({image_file}) now"
    blocks = prompt_api._extract_local_images(message)

    assert len(blocks) == 1
    assert blocks[0]["file_path"] == str(image_file)
    assert blocks[0]["mime"] == "image/jpeg"


def test_mermaid_generation_instructions_require_conservative_flowchart_syntax():
    instructions = prompt_api.MERMAID_GENERATION_INSTRUCTIONS

    assert "`flowchart LR` or `flowchart TD`" in instructions
    assert "`Q -->|Yes| A`" in instructions
    assert "Never use the ambiguous `-- label -->` form." in instructions
    assert "Do not use HTML (including `<br/>`)" in instructions


async def _owned_conversation(*args):
    from types import SimpleNamespace

    return SimpleNamespace(id=1)


@pytest.mark.asyncio
async def test_running_prompt_accepts_followup_without_starting_second_run(monkeypatch):
    from types import SimpleNamespace

    from crabagent.core.agent.context import AgentContext

    context = AgentContext(workspace=Path.cwd())
    context.metadata["_accept_followups"] = True
    monkeypatch.setattr(prompt_api, "get_owned_conversation", _owned_conversation)
    monkeypatch.setattr(prompt_api, "_tasks", {"session": SimpleNamespace(done=lambda: False)})
    monkeypatch.setattr(prompt_api, "_contexts", {"session": context})
    result = await prompt_api.prompt_async(
        "session",
        prompt_api.PromptRequest(message="new instructions", images=["data:image/png;base64,eA=="]),
        None,
        SimpleNamespace(id=1),
        None,
    )
    assert result["status"] == "queued"
    assert context.metadata["_pending_user_messages"][0][0]["text"] == "new instructions"
    block = context.metadata["_pending_user_messages"][0][1]
    assert Path(block["file_path"]).read_bytes() == b"x"
    assert context.metadata["attached_image_paths"] == [block["file_path"]]
    context.model = "gpt-4o"
    context.messages = [{"role": "user", "content": context.metadata["_pending_user_messages"][0]}]
    from crabagent.core.agent.loop import _build_messages
    assert block["file_path"] in _build_messages(context)[0]["content"][2]["text"]


@pytest.mark.asyncio
async def test_finishing_prompt_does_not_accept_followup(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from crabagent.core.agent.context import AgentContext

    context = AgentContext(workspace=Path.cwd())
    monkeypatch.setattr(prompt_api, "get_owned_conversation", _owned_conversation)
    monkeypatch.setattr(prompt_api, "_tasks", {"session": SimpleNamespace(done=lambda: False)})
    monkeypatch.setattr(prompt_api, "_contexts", {"session": context})
    with pytest.raises(HTTPException) as exc:
        await prompt_api.prompt_async("session", prompt_api.PromptRequest(message="hi"), None, None, None)
    assert exc.value.status_code == 409


def test_chat_route_is_served_by_prompt_async():
    """A helper inserted above prompt_async once took over its @router.post decorator,
    and every chat message failed with 422 'query.context: Field required'."""
    from crabagent.serve.api import prompt as prompt_api

    routes = {
        (route.path, method): route.endpoint
        for route in prompt_api.router.routes
        for method in getattr(route, "methods", ())
    }
    assert routes[("/sessions/{session_id}/prompt", "POST")] is prompt_api.prompt_async
    assert all(endpoint is not prompt_api.apply_agent_profile for endpoint in routes.values())


def test_switch_message_hints_follow_the_agents_own_tools(monkeypatch):
    """The saved agent-switch message describes the profile's tools, not the global pool."""
    from crabagent.core.agent.agents import _CAPABILITY_BROWSER, _CAPABILITY_NO_HANDS, capability_hint_lines
    from crabagent.core.agent.tools.registry import ToolRegistry

    pool = ToolRegistry()
    for name in ("read", "grep", "edit", "write", "bash", "computer_observe", "computer_act", "delegate_task"):
        pool.register(name, name, {"type": "object", "properties": {}})(lambda **kwargs: None)
    monkeypatch.setattr(prompt_api, "registry", pool)
    planner = {"tool_permissions": {t: "deny" for t in ("edit", "write", "bash", "computer_observe", "computer_act")}}

    names = prompt_api.profile_tool_names(planner)
    hints = capability_hint_lines(names)

    assert {"read", "grep", "delegate_task"} <= set(names)
    assert not {"edit", "bash", "computer_act", "computer_observe"} & set(names)
    assert _CAPABILITY_NO_HANDS in hints and _CAPABILITY_BROWSER not in hints
    # with no profile restrictions the pool is unchanged
    assert set(prompt_api.profile_tool_names({})) == set(pool._tools)

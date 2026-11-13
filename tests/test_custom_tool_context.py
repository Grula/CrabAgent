"""Contract tests for custom-tool execution context + tool-authoring descriptions.

Two behaviours are pinned here:

(A) A custom tool's ``run`` function may declare an optional ``context``
    parameter and receive the live execution context (``AgentContext``-shaped)
    when it is invoked through ``ToolRegistry.execute(..., context=...)``.
(B) The ``create_tool`` / ``update_tool`` schemas must advertise the supported
    authoring surface: ``async def`` is allowed, ``crabagent`` imports are
    allowed, and an optional ``context`` parameter is available.

Written before the implementation: tests 1, 2, 4 and 5 are expected to fail.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.tool_loader import _make_tool_handler

PARAMS_X = {
    "type": "object",
    "properties": {"x": {"type": "integer"}},
    "required": ["x"],
}


def _fake_context(user_id: int = 7) -> SimpleNamespace:
    """Minimal stand-in for AgentContext.

    Only the attributes ``ToolRegistry.execute`` touches on the non-write-tool
    path are provided; ``tool_permissions``/``approved_tools`` keep the
    permission gate in its default ("auto") branch.
    """
    return SimpleNamespace(
        metadata={"user_id": user_id},
        workspace=Path("/tmp"),
        tool_permissions={},
        approved_tools=set(),
        confirm_callback=None,
    )


def _register(reg: ToolRegistry, name: str, run_fn) -> None:
    reg.register(name=name, description="test tool", parameters=PARAMS_X)(
        _make_tool_handler(run_fn)
    )


@pytest.mark.asyncio
async def test_sync_tool_receives_execution_context():
    """(A) A sync custom tool gets the context passed to registry.execute."""

    def run(x, context=None):
        return str(context.metadata.get("user_id", ""))

    reg = ToolRegistry()
    _register(reg, "t_sync_ctx", run)

    result = await reg.execute("t_sync_ctx", {"x": 1}, context=_fake_context(7))

    assert not str(result).startswith("Error"), f"context was not delivered: {result!r}"
    assert "7" in str(result)


@pytest.mark.asyncio
async def test_async_tool_receives_execution_context():
    """(A) An async custom tool gets the context passed to registry.execute."""

    async def run(x, context=None):
        return str(context.metadata.get("user_id", ""))

    reg = ToolRegistry()
    _register(reg, "t_async_ctx", run)

    result = await reg.execute("t_async_ctx", {"x": 1}, context=_fake_context(7))

    assert not str(result).startswith("Error"), f"context was not delivered: {result!r}"
    assert "7" in str(result)


@pytest.mark.asyncio
async def test_tool_without_context_param_still_works():
    """Regression guard: a custom tool that does not declare context keeps working."""

    def run(x):
        return "ok" + str(x)

    reg = ToolRegistry()
    _register(reg, "t_no_ctx", run)

    result = await reg.execute("t_no_ctx", {"x": 1}, context=_fake_context(7))

    assert result == "ok1"


def _code_description(tool_name: str) -> str:
    from crabagent.core.agent.tools import custom_tool  # noqa: F401  (ensures registration)

    from crabagent.core.agent.tools.registry import registry

    info = registry.get(tool_name)
    assert info is not None, f"{tool_name} is not registered"
    properties = (info.parameters or {}).get("properties", {})
    assert "code" in properties, f"{tool_name} has no 'code' parameter"
    return properties["code"]["description"]


def test_create_tool_description_documents_async_and_context():
    """(B) create_tool must advertise async run(), crabagent imports and context."""
    desc = _code_description("create_tool")
    lowered = desc.lower()

    assert "async def" in lowered, f"missing 'async def' in: {desc!r}"
    assert "context" in lowered, f"missing 'context' in: {desc!r}"
    assert "standard library" not in lowered, (
        f"description still restricts imports to the standard library: {desc!r}"
    )


def test_update_tool_description_documents_async_and_context():
    """(B) update_tool mirrors create_tool's authoring capabilities."""
    desc = _code_description("update_tool")
    lowered = desc.lower()

    assert "async def" in lowered, f"missing 'async def' in: {desc!r}"
    assert "context" in lowered, f"missing 'context' in: {desc!r}"
    assert "standard library" not in lowered, (
        f"description still restricts imports to the standard library: {desc!r}"
    )

"""Custom tools live globally (every project) by default; a project can override."""

from __future__ import annotations

from pathlib import Path

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools import custom_tool
from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.tool_loader import discover_and_register_tools

CODE = "def run(text: str) -> str:\n    return '{label}:' + text\n"


@pytest.fixture
def home(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _write_tool(folder: Path, name: str, label: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.py").write_text(
        f'name = "{name}"\ndescription = "d"\nparameters = {{}}\nrequires_permission = False\n\n'
        + CODE.format(label=label)
    )


def _context(workspace: Path) -> AgentContext:
    context = AgentContext(workspace=workspace)
    context.tool_registry = ToolRegistry()
    return context


def test_global_tools_load_in_every_project(home, tmp_path):
    _write_tool(home / ".crabagent" / "tools", "find_refs", "global")
    for project in ("alpha", "beta"):
        registry = ToolRegistry()
        discover_and_register_tools(registry, tmp_path / project)
        assert registry.get("find_refs").handler(text="x") == "global:x"


def test_a_project_tool_overrides_the_global_one(home, tmp_path):
    _write_tool(home / ".crabagent" / "tools", "find_refs", "global")
    _write_tool(tmp_path / "alpha" / ".crabagent" / "tools", "find_refs", "alpha")

    registry = ToolRegistry()
    discover_and_register_tools(registry, tmp_path / "alpha")

    assert registry.get("find_refs").handler(text="x") == "alpha:x"


def test_underscore_files_are_skipped(home, tmp_path):
    _write_tool(home / ".crabagent" / "tools", "_hello", "sample")
    registry = ToolRegistry()
    discover_and_register_tools(registry, tmp_path / "alpha")
    assert registry.get("_hello") is None


async def test_create_tool_saves_globally_by_default(home, tmp_path):
    context = _context(tmp_path / "alpha")

    result = await custom_tool.create_tool("shout", "Shout text", CODE.format(label="s"), context=context)

    assert "every project" in result
    assert (home / ".crabagent" / "tools" / "shout.py").exists()
    assert not (tmp_path / "alpha" / ".crabagent" / "tools" / "shout.py").exists()


async def test_create_tool_can_keep_a_tool_to_one_project(home, tmp_path):
    context = _context(tmp_path / "alpha")

    await custom_tool.create_tool("local_tool", "d", CODE.format(label="p"), scope="project", context=context)

    assert (tmp_path / "alpha" / ".crabagent" / "tools" / "local_tool.py").exists()
    assert not (home / ".crabagent" / "tools" / "local_tool.py").exists()


async def test_update_and_delete_find_a_global_tool_from_any_project(home, tmp_path):
    await custom_tool.create_tool("shout", "d", CODE.format(label="v1"), context=_context(tmp_path / "alpha"))
    other = _context(tmp_path / "beta")

    assert "already exists" in await custom_tool.create_tool("shout", "d", CODE.format(label="x"), context=other)
    assert "updated successfully" in await custom_tool.update_tool("shout", CODE.format(label="v2"), context=other)
    assert "v2" in (home / ".crabagent" / "tools" / "shout.py").read_text()
    await custom_tool.delete_tool("shout", context=other)
    assert not (home / ".crabagent" / "tools" / "shout.py").exists()

"""Precise Python navigation through the language server (code_index/lsp.py, code_nav, code_search)."""

from __future__ import annotations

from pathlib import Path

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.code_nav import code_nav
from crabagent.core.agent.tools.code_search import code_search
from crabagent.core.code_index import lsp
from crabagent.core.code_index.references import python_definitions

pytestmark = pytest.mark.skipif(lsp.server_command() is None, reason="no Python language server installed")

REGISTRY = '''\
class Registry:
    def execute(self, name):
        return name


class Database:
    def execute(self, sql):
        return sql
'''

APP = '''\
from shop.registry import Database, Registry


class Context:
    def __init__(self) -> None:
        self.tools = Registry()


def run(ctx: Context, db: Database) -> None:
    ctx.tools.execute("read")
    db.execute("select 1")
    db.execute("select 2")
'''


@pytest.fixture
async def ws(tmp_path):
    pkg = tmp_path / "src" / "shop"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "registry.py").write_text(REGISTRY)
    (pkg / "app.py").write_text(APP)
    yield tmp_path
    await lsp.close_all()  # servers belong to this test's event loop


def _ctx(ws: Path) -> AgentContext:
    return AgentContext(workspace=ws)


async def test_class_qualified_references_are_exact(ws):
    result = await code_search("Registry.execute", kind="reference", context=_ctx(ws))
    assert "[precise: Registry.execute, via basedpyright]" in result
    assert "src/shop/app.py:10" in result            # ctx.tools.execute, typed through the attribute
    assert "src/shop/app.py:11" not in result and "src/shop/app.py:12" not in result  # db.execute


async def test_a_name_with_several_definitions_falls_back_and_says_how(ws):
    result = await code_search("execute", kind="reference", context=_ctx(ws))
    assert "2 Python definitions are called 'execute' (Database.execute, Registry.execute)" in result
    assert "search one as Class.execute" in result


async def test_go_to_definition_from_a_call_site(ws):
    result = await code_nav("definition", "src/shop/app.py", 10, "execute", context=_ctx(ws))
    assert result.splitlines()[1].startswith("src/shop/registry.py:2")
    db = await code_nav("definition", "src/shop/app.py", 11, "execute", context=_ctx(ws))
    assert db.splitlines()[1].startswith("src/shop/registry.py:7")


async def test_files_changed_behind_the_servers_back_are_seen(ws):
    ctx = _ctx(ws)
    first = await code_nav("references", "src/shop/registry.py", 7, "execute", context=ctx)
    assert first.startswith("3 references")  # definition + two db.execute calls
    app = ws / "src" / "shop" / "app.py"
    app.write_text(APP + '    db.execute("select 3")\n')  # an agent's edit: no editor event
    second = await code_nav("references", "src/shop/registry.py", 7, "execute", context=ctx)
    assert second.startswith("4 references") and "src/shop/app.py:13" in second


async def test_without_a_language_server_name_matching_says_so(ws, monkeypatch):
    monkeypatch.setattr(lsp, "server_command", lambda: None)
    result = await code_search("Registry.execute", kind="reference", context=_ctx(ws))
    assert "[by name: install basedpyright for precise Python results]" in result
    assert "no Python language server installed" in await code_nav(
        "definition", "src/shop/app.py", 10, "execute", context=_ctx(ws)
    )


async def test_code_nav_input_errors(ws):
    (ws / "web.ts").write_text("export const a = 1;\n")
    ctx = _ctx(ws)
    assert "works on Python files" in await code_nav("definition", "web.ts", 1, "a", context=ctx)
    assert "doesn't appear on line 1" in await code_nav("definition", "src/shop/app.py", 1, "nope", context=ctx)
    assert "action must be" in await code_nav("jump", "src/shop/app.py", 1, "Registry", context=ctx)


def test_definitions_carry_their_class(tmp_path):
    (tmp_path / "m.py").write_text(REGISTRY)
    found = python_definitions(tmp_path, "execute")
    assert sorted(d.qualname for d in found) == ["Database.execute", "Registry.execute"]
    assert [d.qualname for d in python_definitions(tmp_path, "execute", "Database")] == ["Database.execute"]
    assert all(d.column == 8 for d in found)  # where "execute" starts on the def line


async def test_untyped_calls_stay_in_the_answer_as_name_matches(ws):
    (ws / "src" / "shop" / "loose.py").write_text("def go(reg):\n    reg.execute('x')\n\n\ndef also(registry):\n"
                                                  "    registry.execute('y')\n")
    result = await code_search("Registry.execute", kind="reference", context=_ctx(ws))
    assert "src/shop/app.py:10  ctx.tools.execute" in result  # exact
    assert "src/shop/loose.py:6  (by name) registry.execute" in result  # untyped, receiver named like the class
    assert "loose.py:2" not in result  # `reg` doesn't look like a Registry: left out
    assert "more matched by name only" in result

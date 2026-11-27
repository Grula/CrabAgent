"""code_search kind="reference" (core/code_index/references.py)."""

from pathlib import Path

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.code_search import code_search
from crabagent.core.code_index.references import find_references, narrow_to_qualifier

PY_LIB = '''\
class ToolRegistry:
    def execute(self, name):
        return name


def helper():
    return 1


HELPER_ALIAS = helper
'''

PY_USER = '''\
from lib import ToolRegistry, helper
import lib.helper as h2


def run(db, registry):
    db.execute("select 1")
    registry.execute("read")
    value = helper()
    callbacks = [helper]
    # helper() in a comment is not code
    return value, callbacks, "helper"
'''

TSX = '''\
import { Panel } from "./Panel";

export function Panel(props: Props) {
  return null;
}

export function Page() {
  // <Panel /> in a comment
  const x = Panel;
  return (
    <Panel
      title="t"
    />
  );
}
'''


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "lib.py").write_text(PY_LIB, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "user.py").write_text(PY_USER, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_user.py").write_text("from lib import helper\n\nhelper()\n", encoding="utf-8")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "Page.tsx").write_text(TSX, encoding="utf-8")
    return tmp_path


def _kinds(refs, path):
    return {(r.line, r.kind) for r in refs if r.path == path}


def test_python_uses_are_classified_by_ast(ws):
    refs = find_references(ws, "helper")
    assert _kinds(refs, "lib.py") == {(6, "definition"), (10, "reference")}
    # Comments and strings are not uses; an import alias of lib.helper is an import.
    assert _kinds(refs, "src/user.py") == {(1, "import"), (2, "import"), (8, "call"), (9, "reference")}


def test_production_code_comes_before_tests_within_a_kind(ws):
    calls = [r.path for r in find_references(ws, "helper") if r.kind == "call"]
    assert calls == ["src/user.py", "tests/test_user.py"]


def test_tsx_jsx_tags_count_as_calls_and_comments_are_skipped(ws):
    refs = find_references(ws, "Panel")
    assert _kinds(refs, "web/Page.tsx") == {(1, "import"), (3, "definition"), (9, "reference"), (11, "call")}


def test_a_qualified_name_keeps_only_matching_receivers(ws):
    refs = find_references(ws, "execute")
    kept, hidden = narrow_to_qualifier(refs, "ToolRegistry")
    assert hidden == 1
    assert {(r.path, r.line) for r in kept} == {("lib.py", 2), ("src/user.py", 7)}


def test_unmatched_qualifier_hides_nothing(ws):
    refs = find_references(ws, "execute")
    assert narrow_to_qualifier(refs, "Database") == (refs, 0)


@pytest.mark.asyncio
async def test_code_search_reference_kind(ws):
    ctx = AgentContext(workspace=ws)
    result = await code_search("ToolRegistry.execute", kind="reference", context=ctx)
    assert result.startswith("'execute': 1 definition, 1 call")
    # `registry` is untyped: the call is kept as a name match, db.execute is not.
    assert "src/user.py:7  (by name)" in result or "src/user.py:7" in result
    assert "db.execute" not in result

    scoped = await code_search("helper", kind="reference", path_prefix="./tests", context=ctx)
    assert "tests/test_user.py:3" in scoped and "src/user.py" not in scoped

    assert "No references to 'nothing_here'" in await code_search("nothing_here", kind="reference", context=ctx)
    assert (await code_search("   ", kind="reference", context=ctx)).startswith("Error")


def test_very_long_lines_are_skipped_quickly(tmp_path):
    import time

    # Review finding: a 40k-character identifier-like line took ~5 s (quadratic backtracking).
    (tmp_path / "big.ts").write_text("export const X = a" + "b" * 400_000 + ";\ntarget();\n", encoding="utf-8")
    started = time.monotonic()
    refs = find_references(tmp_path, "target")
    assert time.monotonic() - started < 2
    assert [(r.path, r.line, r.kind) for r in refs] == [("big.ts", 2, "call")]


def test_python_too_deep_for_ast_falls_back_to_patterns(tmp_path):
    (tmp_path / "deep.py").write_text("x = " + " + ".join(["1"] * 20_000) + "\ntarget()\n", encoding="utf-8")
    refs = find_references(tmp_path, "target")
    assert [(r.line, r.kind) for r in refs] == [(2, "call")]

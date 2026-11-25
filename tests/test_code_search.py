"""Tests for the ``code_search`` tool: ranking, filters, rendering and registration.

Every test indexes a throwaway workspace into a throwaway database: the index
store's module-level ``DB_PATH`` is monkeypatched, so nothing touches
``~/.crabagent/code_index.db`` or the real repository.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from crabagent.core.agent.tools import code_search as cs
from crabagent.core.agent.tools.registry import registry
from crabagent.core.code_index import Match, store

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def index_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the index at a throwaway file; no test can see the real one."""
    db = tmp_path / "state" / "code_index.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    return db


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A small but realistic workspace: app sources plus a noisy test file."""
    root = tmp_path / "ws"
    _write(
        root,
        "src/app/registry.py",
        'class ToolRegistry:\n'
        '    """Dispatch tool calls by name."""\n'
        "\n"
        "    def execute(self, name: str) -> str:\n"
        '        """Run a tool by name and return its result."""\n'
        "        return name\n"
        "\n"
        "\n"
        "def build_registry() -> ToolRegistry:\n"
        '    """Create the default registry."""\n'
        "    return ToolRegistry()\n",
    )
    # The same identifier repeated many times: on bm25 alone this chunk would
    # outrank the definition, which is what the exact-name tier must survive.
    _write(
        root,
        "tests/test_registry.py",
        "from app.registry import ToolRegistry\n"
        "\n"
        "\n"
        "def test_execute_passes_through():\n"
        + '    # ToolRegistry execute ToolRegistry execute ToolRegistry execute\n' * 20
        + '    assert ToolRegistry().execute("x") == "x"\n',
    )
    _write(
        root,
        "src/app/combinator.py",
        'class ZebraCombinator:\n'
        '    """Combine zebra stripes."""\n'
        "\n"
        "    def run(self) -> str:\n"
        '        return "zebra"\n',
    )
    _write(
        root,
        "tests/test_combinator.py",
        "def test_stripes():\n"
        + "    # zebra zebra zebra zebra zebra zebra zebra zebra\n" * 10
        + "    assert True\n",
    )
    # Five zebracorn symbols in one file: the per-file cap must trim them.
    handlers = "".join(
        f'def zebracorn_{name}():\n    """zebracorn {name} handler."""\n    return "{name}"\n\n\n'
        for name in ("alpha", "beta", "gamma", "delta", "epsilon")
    )
    _write(root, "src/app/handlers.py", handlers)
    _write(
        root,
        "tests/test_handlers.py",
        "def test_zebracorn():\n"
        + "    # zebracorn zebracorn zebracorn zebracorn zebracorn\n" * 6
        + "    assert True\n",
    )
    return root


@pytest.fixture
def context(workspace: Path) -> types.SimpleNamespace:
    """The attributes ``ToolRegistry.execute`` reads off an agent context."""
    return types.SimpleNamespace(
        workspace=str(workspace),
        tool_permissions={},
        approved_tools=set(),
        confirm_callback=None,
        metadata={},
    )


async def _run(ctx: types.SimpleNamespace, **kwargs) -> str:
    return await cs.code_search(context=ctx, **kwargs)


def _headers(output: str) -> list[str]:
    """Result lines that start a hit (not snippets, not the footer, not blanks)."""
    return [
        line
        for line in output.splitlines()
        if line and not line.startswith(" ") and not line.startswith("…")
    ]


# ── ranking ────────────────────────────────────────────────────────────────


async def test_exact_symbol_name_ranks_first_despite_a_louder_chunk(context):
    output = await _run(context, query="ToolRegistry")

    first = _headers(output)[0]
    assert first.startswith("src/app/registry.py:1-6")
    assert "class ToolRegistry" in first
    assert "Dispatch tool calls by name." in first


async def test_prefix_name_ranks_above_a_plain_bm25_chunk(context):
    # "zebra" is only a prefix of ZebraCombinator, and the noisy chunk matches
    # the token literally; the prefix-name tier still puts the class first.
    output = await _run(context, query="zebra")

    headers = _headers(output)
    assert headers[0].startswith("src/app/combinator.py")
    assert "ZebraCombinator" in headers[0]
    assert any(header.startswith("tests/test_combinator.py") for header in headers)


async def test_symbol_hit_carries_its_signature_line(context):
    output = await _run(context, query="ToolRegistry")

    lines = output.splitlines()
    index = next(i for i, line in enumerate(lines) if "class ToolRegistry" in line)
    assert lines[index + 1].startswith("  class ToolRegistry:")


async def test_repeated_calls_are_byte_identical(context):
    first = await _run(context, query="zebracorn")
    second = await _run(context, query="zebracorn")

    assert first == second


# ── multi-word queries: exact tier needs a strict majority ────────────────


@pytest.fixture
def multiword_workspace(tmp_path: Path) -> Path:
    """The shapes behind the two real-run regressions.

    ``workspace tree`` used to return a test helper's ``workspace()`` and ``diff
    preview`` a skill script's ``diff()``: each shares one word with the query,
    while the module the user meant covers both.
    """
    root = tmp_path / "multi"
    _write(
        root,
        "src/crabagent/core/agent/workspace_tree.py",
        "def build_workspace_tree(workspace: str, max_entries: int = 200) -> str:\n"
        '    """Render the workspace as an indented tree, capped at max_entries."""\n'
        "    return workspace\n"
        "\n"
        "\n"
        "def skip_entry(name: str) -> bool:\n"
        '    """Skip dotfiles and build output."""\n'
        "    return name.startswith('.')\n",
    )
    _write(
        root,
        "tests/conftest.py",
        "import pytest\n"
        "\n"
        "\n"
        "@pytest.fixture\n"
        "def workspace(tmp_path):\n"
        '    """A throwaway workspace directory."""\n'
        "    return tmp_path\n",
    )
    _write(
        root,
        "src/crabagent/core/agent/tools/diff_preview.py",
        "class DiffPreview:\n"
        '    """A rendered diff plus its counters."""\n'
        "\n"
        "\n"
        "def build_diff_preview(old_text: str, new_text: str) -> DiffPreview:\n"
        '    """Build the diff preview shown before a write is confirmed."""\n'
        "    return DiffPreview()\n",
    )
    _write(
        root,
        "src/skills/web-performance/scripts/mem_probe.py",
        "def diff(a: dict, b: dict, top: int = 15) -> dict:\n"
        '    """Diff two profile snapshots."""\n'
        "    return {}\n",
    )
    _write(
        root,
        "src/app/background.py",
        "async def cancel(context, job_id: str) -> None:\n"
        '    """Cancel a background job."""\n'
        "    return None\n",
    )
    # A chunk that repeats "cancel" many times: on bm25 alone it would outrank
    # the definition, so the exact tier (which a single-word query still gets)
    # must survive.
    _write(
        root,
        "tests/test_cancel.py",
        "def test_cancel():\n"
        + "    # cancel cancel cancel cancel cancel cancel cancel cancel\n" * 10
        + "    assert True\n",
    )
    _write(
        root,
        "src/app/registry.py",
        "class ToolRegistry:\n"
        '    """Dispatch tool calls by name."""\n'
        "\n"
        "    def execute(self, name: str) -> str:\n"
        '        """Run a tool by name."""\n'
        "        return name\n",
    )
    return root


@pytest.fixture
def multiword_context(multiword_workspace: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        workspace=str(multiword_workspace),
        tool_permissions={},
        approved_tools=set(),
        confirm_callback=None,
        metadata={},
    )


async def test_two_word_query_prefers_the_module_over_a_one_word_function(multiword_context):
    output = await _run(multiword_context, query="workspace tree")

    headers = _headers(output)
    assert headers[0].startswith("src/crabagent/core/agent/workspace_tree.py"), output
    assert "build_workspace_tree" in headers[0]
    # The one-word function is still a hit, just no longer the first one.
    assert any("function workspace" in header for header in headers), output


async def test_two_word_query_prefers_the_module_over_a_script_function(multiword_context):
    output = await _run(multiword_context, query="diff preview")

    headers = _headers(output)
    assert headers[0].startswith("src/crabagent/core/agent/tools/diff_preview.py"), output
    script = [i for i, header in enumerate(headers) if header.startswith("src/skills/")]
    preview = [
        i
        for i, header in enumerate(headers)
        if header.startswith("src/crabagent/core/agent/tools/diff_preview.py")
    ]
    assert preview and (not script or script[0] > preview[0]), output


async def test_single_word_query_still_gets_the_exact_tier(multiword_context):
    output = await _run(multiword_context, query="cancel")

    headers = _headers(output)
    assert headers[0].startswith("src/app/background.py"), output
    assert "function cancel" in headers[0]
    assert any(header.startswith("tests/test_cancel.py") for header in headers)


async def test_name_covering_every_query_word_keeps_the_exact_tier(multiword_context):
    output = await _run(multiword_context, query="ToolRegistry.execute", kind="symbol")

    headers = _headers(output)
    assert headers, output
    assert "method ToolRegistry.execute" in headers[0]


def _symbol(name: str) -> Match:
    return Match(
        source="symbol",
        path="src/app/somewhere.py",
        kind="function",
        name=name,
        qualname=name,
        start_line=1,
        end_line=2,
        signature=f"def {name}():",
        doc="",
        snippet="",
        score=-1.0,
    )


def test_half_the_query_words_is_not_a_strict_majority():
    """Exactly 0.5 must NOT be exact: with ``>=``, ``workspace`` would win
    ``workspace tree`` again and ``diff`` would win ``diff preview``, the two
    results the strict-majority rule exists to fix."""
    one_of_two = ["workspace", "tree"]
    forms = cs._name_forms("workspace")
    assert cs._covers(forms, one_of_two, prefix=False) is False
    assert cs._covers(forms, one_of_two, prefix=True) is False
    assert cs._name_tier(_symbol("workspace"), one_of_two) == 2

    # 1 of 1 and 2 of 2 are covered; the boundary sits strictly above a half.
    assert cs._covers(forms, ["workspace"], prefix=False) is True
    two_of_two = cs._name_forms("build_workspace_tree")
    assert cs._covers(two_of_two, ["workspace", "tree"], prefix=False) is True
    assert cs._name_tier(_symbol("build_workspace_tree"), one_of_two) == 1


# ── filters ────────────────────────────────────────────────────────────────


async def test_kind_symbol_returns_only_symbols_and_finds_a_definition(context):
    output = await _run(context, query="ToolRegistry.execute", kind="symbol")

    headers = _headers(output)
    assert headers, output
    assert all("  text" not in header for header in headers)
    assert headers[0].startswith("src/app/registry.py")
    assert "method ToolRegistry.execute" in headers[0]


async def test_kind_text_excludes_symbols(context):
    output = await _run(context, query="ToolRegistry", kind="text")

    headers = _headers(output)
    assert headers, output
    for header in headers:
        assert re.fullmatch(r"\S+:\d+-\d+ {2}text", header), header


async def test_specific_symbol_kind_filters_the_rest(context):
    output = await _run(context, query="ToolRegistry", kind="class")

    headers = _headers(output)
    assert headers
    assert all(" class ToolRegistry" in header for header in headers)


async def test_unknown_kind_is_an_actionable_error(context):
    output = await _run(context, query="ToolRegistry", kind="bogus")

    assert output.startswith("Error: unknown kind 'bogus'")
    assert "function" in output and "text" in output


async def test_path_prefix_filters_to_src_or_tests(context):
    src = await _run(context, query="ToolRegistry", path_prefix="src/")
    tests = await _run(context, query="ToolRegistry", path_prefix="tests/")

    assert _headers(src) and all(header.startswith("src/") for header in _headers(src))
    assert _headers(tests) and all(header.startswith("tests/") for header in _headers(tests))


# ── limits and footer ─────────────────────────────────────────────────────


async def test_per_file_cap_keeps_at_most_three_hits(context):
    output = await _run(context, query="zebracorn", kind="symbol", limit=10)

    handlers = [header for header in _headers(output) if header.startswith("src/app/handlers.py")]
    assert len(handlers) == 3
    assert "…" not in output  # 3 hits fit, so nothing is reported as truncated


async def test_limit_is_respected_and_footer_only_appears_when_truncated(context):
    output = await _run(context, query="zebracorn", limit=2)

    assert len(_headers(output)) == 2
    footer = output.splitlines()[-1]
    assert re.fullmatch(r"… 2 of \d+ shown", footer), output
    assert int(footer.split()[3]) > 2


async def test_no_footer_when_every_hit_fits(context):
    output = await _run(context, query="ToolRegistry", limit=50)

    assert "shown" not in output


async def test_no_matches_is_one_actionable_line(context):
    output = await _run(context, query="nonexistentzqx")

    assert output == (
        "No matches for 'nonexistentzqx' — try grep for literal text or a shorter query."
    )


# ── reindex ───────────────────────────────────────────────────────────────


async def test_reindex_forces_a_rebuild_and_a_plain_call_does_not(
    context, monkeypatch: pytest.MonkeyPatch
):
    real = cs.ensure_indexed
    stats: list = []

    async def spy(workspace, *, force: bool = False):
        result = await real(workspace, force=force)
        stats.append(result)
        return result

    monkeypatch.setattr(cs, "ensure_indexed", spy)

    await _run(context, query="ToolRegistry", reindex=False)
    await _run(context, query="ToolRegistry")
    await _run(context, query="ToolRegistry", reindex=True)

    assert [stat.rebuilt for stat in stats] == [True, False, True]


# ── FTS escaping ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("query", ["ToolRegistry.execute(", 'a"b', "a*b OR (", "name:"])
async def test_regex_and_fts_metacharacters_do_not_raise(context, query: str):
    output = await _run(context, query=query)

    assert isinstance(output, str) and output
    assert not output.startswith("Error executing")


async def test_parenthesis_query_still_finds_the_definition(context):
    output = await _run(context, query="ToolRegistry.execute(")

    assert "method ToolRegistry.execute" in _headers(output)[0]


# ── registration ──────────────────────────────────────────────────────────


def test_tool_is_registered_with_the_documented_parameters():
    tool = registry.get("code_search")
    assert tool is not None
    assert tool.requires_permission is False

    defs = {definition["function"]["name"]: definition for definition in registry.tool_defs()}
    schema = defs["code_search"]["function"]["parameters"]
    assert schema["required"][0] == "query"  # "summary" is injected by tool_defs
    for name in ("query", "path_prefix", "kind", "limit", "reindex"):
        assert name in schema["properties"]
    assert "symbol" in schema["properties"]["kind"]["description"]
    assert "text" in schema["properties"]["kind"]["description"]


def test_core_init_imports_the_tool_module():
    """A tool is inert without the import in ``crabagent.core`` (see AGENTS.md)."""
    code = (
        "import crabagent.core\n"
        "from crabagent.core.agent.tools.registry import registry\n"
        "tool = registry.get('code_search')\n"
        "assert tool is not None, 'code_search not registered'\n"
        "assert tool.requires_permission is False\n"
        "print(tool.name)\n"
    )
    env = os.environ.copy()
    src = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = src + ((os.pathsep + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "code_search"


async def test_runs_through_the_registry_with_the_injected_context(context):
    output = await registry.execute("code_search", {"query": "ToolRegistry"}, context=context)

    assert isinstance(output, str)
    assert "class ToolRegistry" in _headers(output)[0]


async def test_missing_workspace_is_reported(context):
    context.workspace = None

    output = await _run(context, query="ToolRegistry")

    assert output.startswith("Error: no workspace is set")

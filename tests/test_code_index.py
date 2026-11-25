"""Tests for the semantic code index: storage (store.py) and extraction (extract.py).

Every test runs against a real SQLite file under ``tmp_path``: the store's
module-level ``DB_PATH`` is monkeypatched, so nothing touches
``~/.crabagent/code_index.db``.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from crabagent.core.code_index import (
    connect,
    ensure_indexed,
    fts_query,
    iter_matches,
)
from crabagent.core.code_index import extract as ex
from crabagent.core.code_index import store


@pytest.fixture(autouse=True)
def index_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the index at a throwaway file; it is the only database any test sees."""
    db = tmp_path / "state" / "code_index.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    return db


def _write(root: Path, rel: str, content: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


async def _rows(sql: str, params: tuple = ()) -> list[dict]:
    async with store.connect() as conn:
        async with conn.execute(sql, params) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── storage ────────────────────────────────────────────────────────────────


async def test_schema_created_on_a_fresh_path(index_db):
    async with connect() as conn:
        async with conn.execute("SELECT name, type FROM sqlite_master") as cur:
            found = {row["name"]: row["type"] for row in await cur.fetchall()}
        async with conn.execute("SELECT COUNT(*) AS n FROM code_index_meta") as cur:
            assert (await cur.fetchone())["n"] == 0  # empty meta drives the first build
    for table in ("code_files", "code_symbols", "code_chunks", "code_index_meta"):
        assert found[table] == "table"
    for fts in ("code_symbols_fts", "code_chunks_fts"):
        assert found[fts] == "table"
    for trigger in (
        "code_symbols_ai",
        "code_symbols_ad",
        "code_symbols_au",
        "code_chunks_ai",
        "code_chunks_ad",
        "code_chunks_au",
    ):
        assert found[trigger] == "trigger"


async def test_wal_mode_and_busy_timeout(index_db):
    async with connect() as conn:
        async with conn.execute("PRAGMA journal_mode") as cur:
            assert (await cur.fetchone())[0] == "wal"
        async with conn.execute("PRAGMA busy_timeout") as cur:
            assert (await cur.fetchone())[0] == 30_000


async def test_deleting_the_database_file_rebuilds_without_error(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "a.py", "def alpha():\n    return 1\n")
    first = await ensure_indexed(root)
    assert first.rebuilt is True
    assert (first.files, first.symbols) == (1, 1)

    index_db.unlink()  # a user wiping the cache must just get a rebuild

    second = await ensure_indexed(root)
    assert second.rebuilt is True
    assert (second.files, second.symbols, second.chunks) == (1, 1, 1)


async def test_too_many_changed_files_falls_back_to_full_rebuild(tmp_path, index_db, monkeypatch):
    root = tmp_path / "ws"
    for i in range(3):
        _write(root, f"m{i}.py", f"def func_{i}():\n    return {i}\n")
    await ensure_indexed(root)
    monkeypatch.setattr(store, "MAX_INCREMENTAL_CHANGES", 1)
    for i in range(3):
        _write(root, f"m{i}.py", f"def func_{i}():\n    return {i}\n\n\ndef extra_{i}():\n    return 0\n")
    stats = await ensure_indexed(root)
    assert stats.rebuilt is True
    assert (stats.files, stats.symbols) == (3, 6)


async def test_missing_workspace_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        await ensure_indexed(tmp_path / "nope")


# ── Python extraction ──────────────────────────────────────────────────────

PY_SOURCE = '''"""Module docstring."""

LIMIT = 10
ITEMS: list[int] = [1, 2, 3]


def alpha(value: int) -> int:
    """Double it."""
    return value * 2


class Widget:
    """A widget."""

    kind = "widget"

    def __init__(self, name: str) -> None:
        self.name = name

    def describe(self) -> str:
        """Return the description."""
        def inner() -> int:
            """Nested helper."""
            return 1
        return self.name
'''


def _line(text: str, needle: str) -> int:
    """1-based line number of the first line containing ``needle``."""
    return next(i for i, line in enumerate(text.splitlines(), start=1) if needle in line)


def test_python_extraction_symbols_lines_and_docstrings():
    symbols = ex.parse_python(PY_SOURCE)
    by_qualname = {s.qualname: s for s in symbols}

    alpha = by_qualname["alpha"]
    assert alpha.kind == "function"
    assert (alpha.start_line, alpha.end_line) == (
        _line(PY_SOURCE, "def alpha"),
        _line(PY_SOURCE, "return value * 2"),
    )
    assert alpha.signature == "def alpha(value: int) -> int:"
    assert alpha.doc == "Double it."

    widget = by_qualname["Widget"]
    assert widget.kind == "class"
    assert (widget.start_line, widget.end_line) == (_line(PY_SOURCE, "class Widget"), len(PY_SOURCE.splitlines()))
    assert widget.doc == "A widget."

    assert by_qualname["Widget.__init__"].kind == "method"
    assert by_qualname["Widget.__init__"].doc == ""
    describe = by_qualname["Widget.describe"]
    assert describe.kind == "method"
    assert describe.doc == "Return the description."
    nested = by_qualname["Widget.describe.inner"]
    assert nested.kind == "function"  # nested in a method body, not a class body
    assert nested.start_line == _line(PY_SOURCE, "def inner")
    assert nested.end_line == _line(PY_SOURCE, "return 1")

    assert by_qualname["LIMIT"].kind == "constant"
    assert by_qualname["ITEMS"].kind == "constant"
    assert "Widget.kind" not in by_qualname  # class attributes are not module constants
    assert "kind" not in by_qualname


async def test_python_symbols_are_indexed_with_line_ranges(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "src/mod.py", PY_SOURCE)
    stats = await ensure_indexed(root)
    assert stats.symbols == len(ex.parse_python(PY_SOURCE))
    rows = await _rows("SELECT s.kind, s.qualname, s.start_line, s.doc FROM code_symbols s ORDER BY s.start_line")
    assert rows[0]["qualname"] == "LIMIT"
    assert rows[0]["start_line"] == _line(PY_SOURCE, "LIMIT = 10")
    assert any(row["qualname"] == "Widget.describe" and row["kind"] == "method" for row in rows)


def test_unparsable_python_is_chunked_but_has_no_symbols():
    broken = "def oops(:\n    pass\n"
    assert ex.parse_python(broken) == []
    extracted = ex.extract("python", broken)
    assert extracted.symbols == ()
    assert len(extracted.chunks) == 1


# ── TypeScript / JavaScript extraction ─────────────────────────────────────

TS_SOURCE = """export class WidgetStore {
  private items: string[] = [];

  constructor(db: Db) {
    this.db = db;
  }

  async load(id: string): Promise<Item> {
    if (id) {
      return this.db.get(id);
    }
    return null;
  }
}

export function buildStore(db: Db): WidgetStore {
  return new WidgetStore(db);
}

export const normalize = (raw: string) => {
  return raw.trim();
};

export const oneLine = (x: number) => x + 1

export interface StoreOptions {
  limit?: number;
}

export type StoreKind = "memory" | "disk";

enum Mode {
  Fast,
}
"""


def test_typescript_extraction_documented_shapes():
    symbols = {s.qualname: s for s in ex.parse_typescript(TS_SOURCE)}

    assert symbols["WidgetStore"].kind == "class"
    assert (symbols["WidgetStore"].start_line, symbols["WidgetStore"].end_line) == (
        _line(TS_SOURCE, "export class WidgetStore"),
        _line(TS_SOURCE, "return null;") + 2,
    )
    assert symbols["WidgetStore.constructor"].kind == "method"
    load = symbols["WidgetStore.load"]
    assert load.kind == "method"
    assert (load.start_line, load.end_line) == (_line(TS_SOURCE, "async load"), _line(TS_SOURCE, "return null;") + 1)

    assert symbols["buildStore"].kind == "function"
    assert symbols["normalize"].kind == "function"
    assert symbols["oneLine"].kind == "function"
    assert (symbols["oneLine"].start_line, symbols["oneLine"].end_line) == (
        _line(TS_SOURCE, "export const oneLine"),
        _line(TS_SOURCE, "export const oneLine"),
    )
    assert symbols["StoreOptions"].kind == "interface"
    assert symbols["StoreKind"].kind == "type"
    assert (symbols["StoreKind"].start_line, symbols["StoreKind"].end_line) == (
        _line(TS_SOURCE, "export type StoreKind"),
        _line(TS_SOURCE, "export type StoreKind"),
    )
    assert symbols["Mode"].kind == "enum"


def test_typescript_heuristic_known_gap_is_pinned():
    """The regex pass cannot see a declaration whose parameters span lines.

    This gap is documented in ``extract.parse_typescript``; pinning it keeps a
    future "improvement" from silently changing what the index reports.
    """
    source = (
        "export const multiline = (\n"
        "  a: string,\n"
        "  b: number,\n"
        ") => {\n"
        "  return a + b;\n"
        "};\n"
    )
    names = [s.name for s in ex.parse_typescript(source)]
    assert "multiline" not in names
    assert names == []
    # The body is still searchable through the text chunks.
    assert "return a + b;" in ex.extract("typescript", source).chunks[0].text


def test_javascript_arrow_and_function_shapes():
    source = "const handler = function (event) {\n  return event;\n};\n\nfunction topLevel() {}\n"
    symbols = {s.name: s for s in ex.parse_typescript(source)}
    assert symbols["handler"].kind == "function"
    assert symbols["topLevel"].kind == "function"
    assert symbols["topLevel"].start_line == 5


# ── Markdown extraction ────────────────────────────────────────────────────


def test_markdown_headings_are_symbols():
    source = "# Title\n\nIntro.\n\n## Section one\n\nbody\n\n### Deep **bold**\n"
    headings = ex.parse_markdown(source)
    assert [(h.name, h.kind, h.start_line) for h in headings] == [
        ("Title", "heading", 1),
        ("Section one", "heading", 5),
        ("Deep **bold**", "heading", 9),
    ]
    assert headings[1].signature == "## Section one"
    assert headings[1].end_line == 5


# ── chunks ─────────────────────────────────────────────────────────────────


async def test_text_chunks_are_60_lines_with_10_overlap(tmp_path, index_db):
    root = tmp_path / "ws"
    body = "\n".join(f"line {i}" for i in range(1, 131)) + "\n"
    _write(root, "long.py", body)
    await ensure_indexed(root)
    rows = await _rows("SELECT start_line, end_line, text FROM code_chunks ORDER BY start_line")
    assert [(row["start_line"], row["end_line"]) for row in rows] == [(1, 60), (51, 110), (101, 130)]
    assert rows[1]["text"].startswith("line 51\n")  # 10 lines of overlap
    assert rows[1]["text"].endswith("line 110")


def test_chunk_text_shape_for_short_and_empty_files():
    assert ex.chunk_text("") == []
    assert [(c.start_line, c.end_line) for c in ex.chunk_text("\n".join(str(i) for i in range(60)))] == [(1, 60)]
    assert [(c.start_line, c.end_line) for c in ex.chunk_text("\n".join(str(i) for i in range(61)))] == [
        (1, 60),
        (51, 61),
    ]


# ── skipped files ──────────────────────────────────────────────────────────


async def test_oversized_and_binary_files_are_skipped(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "ok.py", "def good():\n    return 1\n")
    _write(root, "big.py", "x = 1\n" * 90_000)  # ~540 KB, over the 512 KB cap
    _write(root, "blob.py", b"def hidden():\n    return 1\n\x00\n")
    _write(root, "notes.txt", "not an allowed extension\n")
    stats = await ensure_indexed(root)
    assert stats.files == 1
    assert [row["path"] for row in await _rows("SELECT path FROM code_files")] == ["ok.py"]


async def test_unreadable_file_does_not_raise(tmp_path, index_db):
    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    root = tmp_path / "ws"
    _write(root, "ok.py", "def good():\n    return 1\n")
    secret = _write(root, "secret.py", "def hidden():\n    return 1\n")
    secret.chmod(0o000)
    try:
        stats = await ensure_indexed(root)
    finally:
        secret.chmod(0o644)
    assert stats.files == 1


async def test_noise_directories_are_not_indexed(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "src/keep.py", "def keep():\n    return 1\n")
    _write(root, ".hidden/x.py", "def hidden():\n    return 1\n")
    _write(root, "node_modules/pkg/index.js", "function dep() {}\n")
    _write(root, "dist/bundle.js", "function bundled() {}\n")
    _write(root, "__pycache__/cached.py", "def cached():\n    return 1\n")
    _write(root, "src/crabagent/static/assets/app-123.js", "function generated() {}\n")
    stats = await ensure_indexed(root)
    assert [row["path"] for row in await _rows("SELECT path FROM code_files")] == ["src/keep.py"]
    assert stats.symbols == 1


# ── incremental behaviour ──────────────────────────────────────────────────


async def test_incremental_reparses_only_the_touched_file(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "a.py", "def alpha():\n    return 1\n")
    _write(root, "b.py", "def beta():\n    return 2\n\n\ndef gamma():\n    return 3\n")
    _write(root, "notes.md", "# Notes\n")
    first = await ensure_indexed(root)
    assert first.rebuilt is True
    assert (first.files, first.symbols) == (3, 4)  # 1 function, 2 functions, 1 heading
    before = {row["path"]: row for row in await _rows("SELECT path, sha1, indexed_at FROM code_files")}

    _write(root, "a.py", "def alpha():\n    return 1\n\n\ndef delta():\n    return 4\n")
    second = await ensure_indexed(root)

    assert second.rebuilt is False
    assert second.built_at != first.built_at
    assert (second.files, second.symbols) == (3, 5)  # one new function, nothing else re-read
    after = {row["path"]: row for row in await _rows("SELECT path, sha1, indexed_at FROM code_files")}
    assert after["a.py"]["sha1"] != before["a.py"]["sha1"]
    assert after["a.py"]["indexed_at"] != before["a.py"]["indexed_at"]
    for untouched in ("b.py", "notes.md"):
        assert after[untouched]["indexed_at"] == before[untouched]["indexed_at"]
        assert after[untouched]["sha1"] == before[untouched]["sha1"]
    assert [row["qualname"] for row in await _rows("SELECT qualname FROM code_symbols WHERE qualname LIKE 'd%'")] == [
        "delta"
    ]

    third = await ensure_indexed(root)  # nothing changed: a pure no-op sweep
    assert third.rebuilt is False
    assert third.built_at == second.built_at
    assert (third.files, third.symbols, third.chunks) == (second.files, second.symbols, second.chunks)
    assert {row["indexed_at"] for row in await _rows("SELECT indexed_at FROM code_files")} == {
        after[path]["indexed_at"] for path in after
    }


async def test_force_rebuilds_the_workspace(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "a.py", "def alpha():\n    return 1\n")
    first = await ensure_indexed(root)
    second = await ensure_indexed(root, force=True)
    assert second.rebuilt is True
    assert second.built_at != first.built_at
    assert (second.files, second.symbols) == (first.files, first.symbols)


async def test_deleted_file_rows_and_fts_entries_are_gone(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "keep.py", "def keeper():\n    return 1\n")
    gone = _write(root, "gone.py", "def vanishingzebra():\n    return 2\n")
    first = await ensure_indexed(root)
    assert first.files == 2
    assert any(match.name == "vanishingzebra" for match in await iter_matches(root, ["vanishingzebra"]))

    gone.unlink()
    second = await ensure_indexed(root)

    assert second.rebuilt is False
    assert second.files == 1
    assert [row["path"] for row in await _rows("SELECT path FROM code_files")] == ["keep.py"]
    assert await _rows("SELECT id FROM code_symbols WHERE name = 'vanishingzebra'") == []
    assert await iter_matches(root, ["vanishingzebra"]) == []


# ── workspaces ─────────────────────────────────────────────────────────────


async def test_two_workspaces_in_one_database_are_isolated(tmp_path, index_db):
    left, right = tmp_path / "left", tmp_path / "right"
    _write(left, "one.py", "def leftmarkerzebra():\n    return 1\n")
    _write(right, "two.py", "def rightmarkerzebra():\n    return 2\n")
    left_stats = await ensure_indexed(left)
    right_stats = await ensure_indexed(right)

    assert (left_stats.files, right_stats.files) == (1, 1)
    assert {match.path for match in await iter_matches(left, ["leftmarkerzebra"])} == {"one.py"}
    assert await iter_matches(left, ["rightmarkerzebra"]) == []
    assert {match.path for match in await iter_matches(right, ["rightmarkerzebra"])} == {"two.py"}
    rows = await _rows("SELECT DISTINCT workspace FROM code_files")
    assert len(rows) == 2
    assert len({row["workspace"] for row in rows}) == 2


async def test_concurrent_calls_build_exactly_once(tmp_path, index_db):
    root = tmp_path / "ws"
    for i in range(5):
        _write(root, f"m{i}.py", f"def func_{i}():\n    return {i}\n")

    first, second = await asyncio.gather(ensure_indexed(root), ensure_indexed(root))

    assert (first.files, first.symbols) == (5, 5)
    assert (second.files, second.symbols) == (5, 5)
    assert first.built_at == second.built_at
    # Only one call can have found a cold index: a double build would report
    # rebuilt twice and stamp rows with two different indexed_at values.
    assert [first.rebuilt, second.rebuilt].count(True) == 1
    assert len(await _rows("SELECT DISTINCT indexed_at FROM code_files")) == 1
    async with connect() as conn:
        key = f"last_build:{root.resolve()}"
        async with conn.execute("SELECT value FROM code_index_meta WHERE key = ?", (key,)) as cur:
            assert (await cur.fetchone())["value"] == first.built_at


# ── FTS query surface for task 2 ───────────────────────────────────────────


def test_fts_query_escapes_and_joins_tokens():
    assert fts_query(["alpha", "beta gamma", "  "]) == '"alpha" OR "beta gamma"'
    assert fts_query(["regis"], prefix=True) == '"regis"*'
    assert fts_query(['he said "hi"']) == '"he said ""hi"""'
    assert fts_query([]) == ""


async def test_fts_tables_match_symbols_and_chunks(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(
        root,
        "src/zebra.py",
        'class ZebraCombinator:\n    """Combines zebras."""\n\n'
        "    def run(self) -> int:\n        return 1  # zebramarkerword inside a body\n",
    )
    await ensure_indexed(root)

    matches = await iter_matches(root, ["ZebraCombinator"])
    symbol_hits = [match for match in matches if match.source == "symbol"]
    # The class itself and its method, whose qualified name also contains it.
    assert {match.name for match in symbol_hits} == {"ZebraCombinator", "run"}
    class_hit = next(match for match in symbol_hits if match.kind == "class")
    assert class_hit.path == "src/zebra.py"
    assert class_hit.score < 0  # bm25: smaller is better
    assert next(match for match in symbol_hits if match.kind == "method").qualname == "ZebraCombinator.run"

    chunk_hits = await iter_matches(root, ["zebramarkerword"])
    assert [(match.source, match.kind, match.path) for match in chunk_hits] == [("text", "text", "src/zebra.py")]
    assert "zebramarkerword" in chunk_hits[0].snippet

    method_hits = [match for match in await iter_matches(root, ["run"], kinds=["method"]) if match.source == "symbol"]
    assert [match.qualname for match in method_hits] == ["ZebraCombinator.run"]
    assert [m for m in await iter_matches(root, ["run"], kinds=["class"]) if m.source == "symbol"] == []
    assert len(await iter_matches(root, ["zeb"], prefix=True, path_prefix="src/")) >= 1
    assert await iter_matches(root, ["zeb"], prefix=True, path_prefix="docs/") == []
    assert len(await iter_matches(root, ["zeb"], prefix=True, limit=1)) <= 2  # one per table


async def test_fts_symbol_entry_is_updated_when_the_symbol_changes(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "a.py", "def oldnamezebra():\n    return 1\n")
    await ensure_indexed(root)
    assert await iter_matches(root, ["oldnamezebra"]) != []

    _write(root, "a.py", "def newnamezebra():\n    return 1\n")
    await ensure_indexed(root)

    assert await iter_matches(root, ["oldnamezebra"]) == []
    assert [match.name for match in await iter_matches(root, ["newnamezebra"]) if match.source == "symbol"] == [
        "newnamezebra"
    ]

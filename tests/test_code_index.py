"""Tests for the semantic code index: storage (store.py) and extraction (extract.py).

Every test runs against a real SQLite file under ``tmp_path``: the store's
module-level ``DB_PATH`` is monkeypatched, so nothing touches
``~/.crabagent/code_index.db``.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
from pathlib import Path

import pytest

from crabagent.core.code_index import (
    connect,
    ensure_indexed,
    fts_query,
    iter_matches,
    prune_stale_workspaces,
)
from crabagent.core.code_index import extract as ex
from crabagent.core.code_index import store


@pytest.fixture(autouse=True)
def index_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the index at a throwaway file; it is the only database any test sees."""
    db = tmp_path / "state" / "code_index.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    # The stale-workspace sweep is once per process; give every test a clean run.
    monkeypatch.setattr(store, "_PRUNE_DONE", False)
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


async def test_symlinks_pointing_outside_the_workspace_are_not_indexed(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "keep.json", '{"keep": "insidezebramarker"}\n')
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text('{"secret": "outsidezebrasecret"}\n', encoding="utf-8")
    (root / "link.json").symlink_to(outside / "secret.json")
    (root / "alias.json").symlink_to(root / "keep.json")

    stats = await ensure_indexed(root)

    assert stats.files == 2
    paths = {row["path"] for row in await _rows("SELECT path FROM code_files")}
    assert paths == {"keep.json", "alias.json"}
    # the escaping symlink's content never becomes a search hit
    assert await iter_matches(root, ["outsidezebrasecret"]) == []
    # a link that stays inside the workspace is indexed like its target
    assert {m.path for m in await iter_matches(root, ["insidezebramarker"])} == {"keep.json", "alias.json"}


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


async def test_a_new_file_is_indexed_and_searchable_on_the_next_sweep(tmp_path, index_db):
    """A path with no row yet must not be handed to ``_delete_file_rows``."""
    root = tmp_path / "ws"
    _write(root, "a.py", "def alphazebra():\n    return 1\n")
    assert (await ensure_indexed(root)).rebuilt is True

    _write(root, "b.py", "def betazebra():\n    return 2\n")
    second = await ensure_indexed(root)  # used to raise KeyError on the new path

    assert second.rebuilt is False
    assert (second.files, second.symbols) == (2, 2)
    assert {match.path for match in await iter_matches(root, ["betazebra"])} == {"b.py"}
    assert {match.path for match in await iter_matches(root, ["alphazebra"])} == {"a.py"}


async def test_unindexable_files_are_remembered_and_never_force_a_rebuild(
    tmp_path, index_db, monkeypatch
):
    """Binary/oversized files get no row, so they must not look new every sweep."""
    root = tmp_path / "ws"
    _write(root, "ok.py", "def goodzebra():\n    return 1\n")
    for i in range(3):
        _write(root, f"blob{i}.py", b"\x00\x01binary\x00\x02")
    _write(root, "big.py", "x = 1\n" * 90_000)  # over the 512 KB cap: never scanned
    # Any file counted as changed tips this: three "new" binaries would rebuild.
    monkeypatch.setattr(store, "MAX_INCREMENTAL_CHANGES", 1)

    first = await ensure_indexed(root)
    assert (first.rebuilt, first.files, first.symbols) == (True, 1, 1)

    for sweep in (2, 3):
        stats = await ensure_indexed(root)  # used to raise KeyError on sweep 2
        assert stats.rebuilt is False, f"sweep {sweep} fell back to a full rebuild"
        assert (stats.files, stats.symbols) == (1, 1)


async def test_a_deleted_and_a_new_file_in_the_same_sweep(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "keep.py", "def keeperzebra():\n    return 1\n")
    gone = _write(root, "gone.py", "def gonezebra():\n    return 2\n")
    await ensure_indexed(root)

    gone.unlink()
    _write(root, "fresh.py", "def freshzebra():\n    return 3\n")
    stats = await ensure_indexed(root)

    assert stats.rebuilt is False
    assert stats.files == 2
    assert {row["path"] for row in await _rows("SELECT path FROM code_files")} == {"keep.py", "fresh.py"}
    assert await _rows("SELECT id FROM code_symbols WHERE name = 'gonezebra'") == []
    assert {match.path for match in await iter_matches(root, ["freshzebra"])} == {"fresh.py"}
    assert await iter_matches(root, ["gonezebra"]) == []


async def test_a_sixty_four_kilobyte_line_is_not_scanned(tmp_path, index_db):
    """A minified one-liner is skipped, not scanned (64k of spaces took seconds)."""
    source = "  minifiedMethod(): void" + " " * 65_500 + "{}\n"
    started = time.perf_counter()
    symbols = ex.parse_typescript(source)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"scanning one 64k line took {elapsed:.1f}s"
    assert symbols == [], "a line over MAX_SCAN_LINE_CHARS must not be symbol-scanned"

    root = tmp_path / "ws"
    _write(root, "bundle.ts", source)
    stats = await ensure_indexed(root)  # the whole build completes too
    assert (stats.files, stats.chunks) == (1, 1)


def test_long_but_scannable_lines_stay_linear():
    """Under the cap the regex must be linear: it used to backtrack quadratically."""
    source = "".join("  method()" + " " * 3_900 + "\n" for _ in range(100))  # 100 x 3.9k lines
    started = time.perf_counter()
    symbols = ex.parse_typescript(source)
    elapsed = time.perf_counter() - started
    assert symbols == []
    assert elapsed < 0.5, f"scanning 100 lines of 3.9k chars took {elapsed:.2f}s"


def test_typescript_method_shapes_survive_the_linear_regex():
    """The possessive method pattern must still read real declarations."""
    source = (
        "class Store {\n"
        "  constructor(db: Db) {\n"
        "    this.db = db;\n"
        "  }\n"
        "\n"
        "  async load(id: string): Promise<Item> {\n"
        "    return this.db.get(id);\n"
        "  }\n"
        "\n"
        "  static async run(a, b)  :  Promise<void>   {\n"
        "    return;\n"
        "  }\n"
        "}\n"
    )
    methods = {s.qualname: s for s in ex.parse_typescript(source) if s.kind == "method"}
    assert set(methods) == {"Store.constructor", "Store.load", "Store.run"}


async def test_ensure_indexed_reads_files_off_the_event_loop(tmp_path, index_db, monkeypatch):
    """Walk, reads and parsing run in worker threads, so the loop keeps ticking.

    Every read is slowed to 60 ms — a large file, a cold cache or a slow mount.
    On the event loop each one is an uninterrupted stall; in a worker thread the
    loop is free while it runs. (An ordinary-sized workspace does not show this:
    the per-file ``await`` on the database writes already yields on its own.)
    """
    root = tmp_path / "ws"
    for i in range(20):
        _write(root, f"mod{i}.py", "def f():\n    return 1\n")

    real_read = store.read_source
    read_delay = 0.06

    def slow_read(path):
        time.sleep(read_delay)
        return real_read(path)

    monkeypatch.setattr(store, "read_source", slow_read)

    stamps: list[float] = []

    async def ticker() -> None:
        while True:
            await asyncio.sleep(0.01)
            stamps.append(time.perf_counter())

    task = asyncio.create_task(ticker())
    try:
        await asyncio.sleep(0.05)
        stats = await ensure_indexed(root)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert stats.files == 20
    gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    assert len(stamps) >= 5, f"only {len(stamps)} ticks: the build blocked the loop"
    assert max(gaps) < read_delay / 2, f"event loop stalled for {max(gaps):.3f}s during a read"


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


async def test_a_concurrent_sweep_from_another_process_retries_instead_of_raising(
    tmp_path, index_db, monkeypatch
):
    """Two processes sweeping one workspace: no ``IntegrityError``, coherent rows.

    The per-workspace lock is in-process only, so a second CrabAgent process can
    commit a file row between our read of the existing rows and our write; the
    write then trips ``UNIQUE(workspace, path)``. The hook below plays that
    second process: it lets the real read finish, then inserts ``b.py`` through
    its own connection before the sweep goes on to write.
    """
    root = tmp_path / "ws"
    _write(root, "a.py", "def alpha():\n    return 1\n")
    await ensure_indexed(root)

    _write(root, "b.py", "def beta():\n    return 2\n")
    real_existing = store._existing_files
    injected = {"done": False}

    async def racing_existing(conn, workspace):
        rows = await real_existing(conn, workspace)
        if not injected["done"]:
            injected["done"] = True
            scanned = {f.path: f for f in await asyncio.to_thread(store._scan_files, root)}
            source = store.read_source(root / "b.py")
            async with store.connect() as other:
                await store._insert_one(
                    other,
                    workspace,
                    scanned["b.py"],
                    source,
                    store.extract("python", source.text),
                    123,
                )
                await other.commit()
        return rows

    monkeypatch.setattr(store, "_existing_files", racing_existing)

    stats = await ensure_indexed(root)

    assert injected["done"]  # the race really happened
    assert stats.files == 2
    assert [row["path"] for row in await _rows("SELECT path FROM code_files ORDER BY path")] == [
        "a.py",
        "b.py",
    ]
    orphans = await _rows(
        "SELECT COUNT(*) AS n FROM code_symbols WHERE file_id NOT IN (SELECT id FROM code_files)"
    )
    assert orphans[0]["n"] == 0
    assert len(await iter_matches(root, ["beta"])) >= 1


# ── FTS query surface for task 2 ───────────────────────────────────────────


def test_fts_query_treats_control_characters_as_spaces():
    assert fts_query(["a\x00b"]) == '"a b"'
    assert fts_query(["a\x00b"], prefix=True) == '"a b"*'
    assert fts_query(["alpha", "\x00"]) == '"alpha"'
    assert fts_query(["\x00"]) == ""


async def test_nul_in_the_query_returns_no_hits_instead_of_raising(tmp_path, index_db):
    root = tmp_path / "ws"
    _write(root, "a.py", "def alpha():\n    return 1\n")
    await ensure_indexed(root)

    assert await iter_matches(root, ["a\x00b"]) == []
    assert await iter_matches(root, ["alpha"]) != []


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


# ── stale workspace pruning ────────────────────────────────────────────────


async def _fts_rowids(table: str, term: str) -> list[int]:
    """Rowids a ``MATCH`` returns, so a stale FTS entry cannot hide."""
    rows = await _rows(f"SELECT rowid FROM {table} WHERE {table} MATCH ?", (term,))
    return [row["rowid"] for row in rows]


async def test_prune_drops_a_deleted_workspace_on_another_workspaces_build(tmp_path, index_db, monkeypatch):
    stale, live = tmp_path / "stale", tmp_path / "live"
    _write(stale, "old.py", "def stalemarkerzebra():\n    return 1\n")
    _write(stale, "notes.md", "# stalechunkzebra heading\n")
    _write(live, "keep.py", "def livemarkerzebra():\n    return 2\n")
    await ensure_indexed(stale)
    await ensure_indexed(live)
    stale_key = str(stale.resolve())
    shutil.rmtree(stale)
    monkeypatch.setattr(store, "_PRUNE_DONE", False)  # simulate the first call of a run

    await ensure_indexed(live)  # nothing new for `live`: this call only sweeps

    assert await _rows("SELECT id FROM code_files WHERE workspace = ?", (stale_key,)) == []
    assert await _rows("SELECT id FROM code_symbols WHERE name = 'stalemarkerzebra'") == []
    assert await _rows("SELECT id FROM code_chunks WHERE text LIKE '%stalechunkzebra%'") == []
    assert await _rows("SELECT key FROM code_index_meta WHERE key = ?", (f"last_build:{stale_key}",)) == []
    assert await _fts_rowids("code_symbols_fts", "stalemarkerzebra") == []
    assert await _fts_rowids("code_chunks_fts", "stalechunkzebra") == []
    assert await iter_matches(live, ["stalemarkerzebra"]) == []


async def test_prune_leaves_a_live_workspace_untouched(tmp_path, index_db, monkeypatch):
    stale, live = tmp_path / "stale", tmp_path / "live"
    _write(stale, "old.py", "def staleonezebra():\n    return 1\n")
    _write(live, "keep.py", "def liveonezebra():\n    return 2\n")
    await ensure_indexed(stale)
    live_stats = await ensure_indexed(live)
    live_key = str(live.resolve())
    sql = "SELECT id, workspace, path, mtime_ms, sha1, indexed_at FROM code_files ORDER BY id"
    live_before = [row for row in await _rows(sql) if row["workspace"] == live_key]
    shutil.rmtree(stale)
    monkeypatch.setattr(store, "_PRUNE_DONE", False)  # simulate the first call of a run

    await ensure_indexed(live)

    live_after = [row for row in await _rows(sql) if row["workspace"] == live_key]
    assert live_after == live_before  # no row churn, no new indexed_at
    assert (await ensure_indexed(live)).built_at == live_stats.built_at


async def test_prune_skips_a_workspace_with_a_build_in_flight(tmp_path, index_db, monkeypatch):
    busy = tmp_path / "busy"
    for i in range(3):
        _write(busy, f"m{i}.py", f"def busyfunc_{i}():\n    return {i}\n")
    await ensure_indexed(busy)
    busy_key = str(busy.resolve())

    started = asyncio.Event()
    real_insert = store._insert_files

    async def slow_insert(*args, **kwargs):
        started.set()
        await asyncio.sleep(0.5)
        return await real_insert(*args, **kwargs)

    monkeypatch.setattr(store, "_insert_files", slow_insert)
    monkeypatch.setattr(store, "_workspace_exists", lambda path: False)  # every workspace looks stale

    build = asyncio.create_task(ensure_indexed(busy, force=True))
    await asyncio.wait_for(started.wait(), timeout=5)
    assert store._workspace_busy(busy_key)

    began = time.perf_counter()
    pruned = await prune_stale_workspaces(force=True)
    elapsed = time.perf_counter() - began

    assert pruned == []  # the held lock wins over the stale-looking path
    assert not build.done()  # pruning neither waited for nor disturbed the build
    assert elapsed < 0.3
    stats = await asyncio.wait_for(build, timeout=5)
    assert (stats.files, stats.symbols, stats.chunks) == (3, 3, 3)
    assert len(await _rows("SELECT id FROM code_files WHERE workspace = ?", (busy_key,))) == 3


async def test_prune_runs_once_per_process_until_forced(tmp_path, index_db, monkeypatch):
    gone, live = tmp_path / "gone", tmp_path / "live"
    _write(gone, "old.py", "def oncezebra():\n    return 1\n")
    _write(live, "keep.py", "def livezebra():\n    return 2\n")
    await ensure_indexed(gone)
    await ensure_indexed(live)
    gone_key = str(gone.resolve())
    shutil.rmtree(gone)
    monkeypatch.setattr(store, "_PRUNE_DONE", False)  # simulate the first call of a run

    await ensure_indexed(live)  # the lazy sweep of this process
    assert await _rows("SELECT id FROM code_files WHERE workspace = ?", (gone_key,)) == []

    async with store.connect() as conn:  # a row that reappears must survive the next call
        await conn.execute("INSERT INTO code_files(workspace, path) VALUES (?, 'ghost.py')", (gone_key,))
        await conn.commit()
    await ensure_indexed(live)
    assert await _rows("SELECT id FROM code_files WHERE workspace = ?", (gone_key,)) != []
    assert await prune_stale_workspaces() == []  # already swept: no second pass

    assert await prune_stale_workspaces(force=True) == [gone_key]
    assert await _rows("SELECT id FROM code_files WHERE workspace = ?", (gone_key,)) == []


async def test_prune_never_touches_the_workspace_being_ensured(tmp_path, index_db, monkeypatch):
    root = tmp_path / "self"
    _write(root, "a.py", "def selfmarkerzebra():\n    return 1\n")
    await ensure_indexed(root)
    key = str(root.resolve())
    sql = "SELECT id, indexed_at FROM code_files WHERE workspace = ?"
    rows_before = await _rows(sql, (key,))

    monkeypatch.setattr(store, "_workspace_exists", lambda path: False)  # stale-looking
    monkeypatch.setattr(store, "_PRUNE_DONE", False)
    await ensure_indexed(root)

    assert await _rows(sql, (key,)) == rows_before
    assert await _rows("SELECT key FROM code_index_meta WHERE key = ?", (f"last_build:{key}",)) != []

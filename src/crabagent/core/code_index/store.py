"""SQLite storage and build orchestration for the workspace code index.

The index lives in its **own** database file, ``~/.crabagent/code_index.db``
(override for tests by monkeypatching :data:`DB_PATH`; there is deliberately no
settings field). It never touches ``crabagent.db``.

Design points:

* WAL mode plus ``busy_timeout`` so a reader in another process never sees a
  locked database, and :mod:`aiosqlite` throughout so the build never blocks the
  event loop.
* Every ``CREATE`` uses ``IF NOT EXISTS``, so deleting the file simply means the
  next :func:`ensure_indexed` builds from scratch — there is no error path.
* One file holds several workspaces: every row is keyed by a ``workspace``
  column (the resolved absolute path).
* One build per workspace at a time: an :class:`asyncio.Lock` per resolved
  workspace in a module-level registry, so two concurrent
  :func:`ensure_indexed` calls build once and both return the finished index.
* Stale workspaces (folder deleted) are pruned lazily: the first
  :func:`ensure_indexed` of a process removes their rows, once, skipping any
  workspace that has a build in flight.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .extract import ScannedFile, extract, iter_files, read_source

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"
BUSY_TIMEOUT_MS = 30_000
# Design limit: more changed files than this in one sweep is cheaper to rebuild.
MAX_INCREMENTAL_CHANGES = 200


def default_db_path() -> Path:
    """``$HOME/.crabagent/code_index.db`` (``Path.home()`` honours ``$HOME``)."""
    return Path.home() / ".crabagent" / "code_index.db"


# Monkeypatch this attribute (``store.DB_PATH = tmp_path / "code_index.db"``) to
# redirect the index; everything below reads it through :func:`db_path`.
DB_PATH: Path = default_db_path()


def db_path() -> Path:
    """Current index database path, read at call time so tests can override it."""
    return DB_PATH


_SCHEMA = """
CREATE TABLE IF NOT EXISTS code_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace TEXT NOT NULL,
    path TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT '',
    mtime_ms INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    sha1 TEXT NOT NULL DEFAULT '',
    indexed_at INTEGER NOT NULL DEFAULT 0,
    UNIQUE (workspace, path)
);
CREATE INDEX IF NOT EXISTS ix_code_files_workspace ON code_files(workspace);

CREATE TABLE IF NOT EXISTS code_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    qualname TEXT NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    signature TEXT NOT NULL DEFAULT '',
    doc TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_code_symbols_file ON code_symbols(file_id);

CREATE TABLE IF NOT EXISTS code_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_code_chunks_file ON code_chunks(file_id);

-- FTS5 external content: the index stores rowids only, the text stays in the
-- base tables and the triggers below keep the two in sync.
CREATE VIRTUAL TABLE IF NOT EXISTS code_symbols_fts USING fts5(
    name, qualname, signature, doc,
    content='code_symbols', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS code_symbols_ai AFTER INSERT ON code_symbols BEGIN
    INSERT INTO code_symbols_fts(rowid, name, qualname, signature, doc)
    VALUES (new.id, new.name, new.qualname, new.signature, new.doc);
END;
CREATE TRIGGER IF NOT EXISTS code_symbols_ad AFTER DELETE ON code_symbols BEGIN
    INSERT INTO code_symbols_fts(code_symbols_fts, rowid, name, qualname, signature, doc)
    VALUES ('delete', old.id, old.name, old.qualname, old.signature, old.doc);
END;
CREATE TRIGGER IF NOT EXISTS code_symbols_au AFTER UPDATE ON code_symbols BEGIN
    INSERT INTO code_symbols_fts(code_symbols_fts, rowid, name, qualname, signature, doc)
    VALUES ('delete', old.id, old.name, old.qualname, old.signature, old.doc);
    INSERT INTO code_symbols_fts(rowid, name, qualname, signature, doc)
    VALUES (new.id, new.name, new.qualname, new.signature, new.doc);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS code_chunks_fts USING fts5(
    text, content='code_chunks', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS code_chunks_ai AFTER INSERT ON code_chunks BEGIN
    INSERT INTO code_chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS code_chunks_ad AFTER DELETE ON code_chunks BEGIN
    INSERT INTO code_chunks_fts(code_chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS code_chunks_au AFTER UPDATE ON code_chunks BEGIN
    INSERT INTO code_chunks_fts(code_chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO code_chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS code_index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


@dataclass(frozen=True, slots=True)
class IndexStats:
    """What :func:`ensure_indexed` found after the call."""

    files: int
    symbols: int
    chunks: int
    built_at: str  # ISO-8601 UTC of the last build/write that touched this workspace
    duration_ms: float
    rebuilt: bool  # True when a full (re)build ran, False for the incremental path


@dataclass(frozen=True, slots=True)
class Match:
    """One FTS hit: a symbol row or a text chunk."""

    source: str  # "symbol" | "text"
    path: str  # workspace-relative
    kind: str  # symbol kind, or "text" for a chunk hit
    name: str
    qualname: str
    start_line: int
    end_line: int
    signature: str
    doc: str
    snippet: str
    score: float  # SQLite bm25(): more negative is a better match


# ── connections ────────────────────────────────────────────────────────────


@asynccontextmanager
async def connect(path: Path | None = None) -> AsyncIterator[aiosqlite.Connection]:
    """Open the index database, creating its schema, and close it on exit.

    ``WAL`` journal mode, a 30 s busy timeout and foreign keys on. Safe to call
    on a fresh (or deleted) file: the schema is created with ``IF NOT EXISTS``.
    """
    target = Path(path) if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(target, timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.executescript(_SCHEMA)
        await conn.commit()
        yield conn
    finally:
        await conn.close()


# ── build locks ────────────────────────────────────────────────────────────

_WORKSPACE_LOCKS: dict[str, asyncio.Lock] = {}
_WORKSPACE_LOCKS_GUARD = asyncio.Lock()


async def _workspace_lock(key: str) -> asyncio.Lock:
    """One lock per resolved workspace path, so concurrent builds serialise."""
    async with _WORKSPACE_LOCKS_GUARD:
        lock = _WORKSPACE_LOCKS.get(key)
        if lock is None:
            lock = _WORKSPACE_LOCKS[key] = asyncio.Lock()
        return lock


# ── stale workspace pruning ────────────────────────────────────────────────

# Meta keys that belong to one workspace rather than to the database.
_LAST_BUILD_PREFIX = "last_build:"
# Lazy maintenance: the sweep runs on the first ensure_indexed() of this
# process and not again, so a long-lived server pays for it exactly once.
_PRUNE_DONE = False


def _workspace_exists(path: str) -> bool:
    """True while ``path`` is still a directory (workspaces are stored resolved)."""
    return Path(path).is_dir()


def _workspace_busy(key: str) -> bool:
    """True when a build for ``key`` holds its lock right now.

    Read with a plain ``dict.get``: there is no ``await`` between the lookup and
    ``locked()``, so the event loop cannot switch tasks in between. We never
    *wait* for that lock — a cold build takes seconds and the agent waiting on
    this sweep (through :func:`ensure_indexed`) would stall behind it.
    """
    lock = _WORKSPACE_LOCKS.get(key)
    return lock is not None and lock.locked()


async def _purge_workspace(workspace: str) -> None:
    """Delete one workspace's rows in a single transaction.

    Order matters, because only the ``AFTER DELETE ON code_symbols`` /
    ``code_chunks`` triggers reset the external-content FTS index
    (``INSERT INTO code_symbols_fts(code_symbols_fts, rowid, …) VALUES
    ('delete', old.id, …)``). Verified: deleting only ``code_files`` leaves the
    symbol's row in the FTS index, ``MATCH`` still returns it. So the children
    are deleted explicitly first (firing the ``*_ad`` triggers), then the file
    rows — same order as :func:`_delete_file_rows`.
    """
    async with connect() as conn:
        try:
            await _delete_workspace_rows(conn, workspace)
            await conn.execute(
                "DELETE FROM code_index_meta WHERE key = ?",
                (f"{_LAST_BUILD_PREFIX}{workspace}",),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise


async def prune_stale_workspaces(*, force: bool = False, skip: str | None = None) -> list[str]:
    """Drop the index rows of workspaces whose folder no longer exists.

    Lazy maintenance: called from :func:`ensure_indexed`, it sweeps at most
    once per process unless ``force=True``. A stale workspace never blocks the
    caller — every workspace is pruned in its own transaction, and a failure is
    logged and skipped so one bad row set cannot stop the rest.

    ``skip`` is the workspace the caller is about to build: its lock is not
    held yet, so it has to be excluded explicitly. A workspace whose lock *is*
    held is skipped too: waiting would stall the agent requesting this sweep and
    deleting rows under a running build would corrupt it.

    Returns the workspace paths that were pruned.
    """
    global _PRUNE_DONE
    if _PRUNE_DONE and not force:
        return []
    skip_key = str(Path(skip).expanduser().resolve()) if skip else None
    async with connect() as conn:
        async with conn.execute("SELECT workspace FROM code_files GROUP BY workspace") as cur:
            workspaces = {row["workspace"] for row in await cur.fetchall()}
        async with conn.execute(
            "SELECT key FROM code_index_meta WHERE key LIKE ?",
            (f"{_LAST_BUILD_PREFIX}%",),
        ) as cur:
            workspaces.update(
                row["key"][len(_LAST_BUILD_PREFIX):] for row in await cur.fetchall()
            )

    pruned: list[str] = []
    for workspace in sorted(workspaces):
        if workspace == skip_key or _workspace_busy(workspace) or _workspace_exists(workspace):
            continue
        try:
            await _purge_workspace(workspace)
        except Exception as exc:  # one workspace must never block the others
            logger.warning("code_index: could not prune stale workspace %s: %s", workspace, exc)
            continue
        logger.info("code_index: pruned stale workspace %s", workspace)
        pruned.append(workspace)
    _PRUNE_DONE = True
    return pruned


# ── helpers ────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _now_ns() -> int:
    return time.time_ns()


async def _meta(conn: aiosqlite.Connection) -> dict[str, str]:
    async with conn.execute("SELECT key, value FROM code_index_meta") as cur:
        return {row["key"]: row["value"] for row in await cur.fetchall()}


async def _set_meta(conn: aiosqlite.Connection, key: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO code_index_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


async def _existing_files(conn: aiosqlite.Connection, workspace: str) -> dict[str, aiosqlite.Row]:
    async with conn.execute(
        "SELECT id, path, mtime_ms, size, sha1, indexed_at FROM code_files WHERE workspace = ?",
        (workspace,),
    ) as cur:
        rows = await cur.fetchall()
    return {row["path"]: row for row in rows}


async def _delete_file_rows(conn: aiosqlite.Connection, file_id: int) -> None:
    """Delete a file and its symbols/chunks; the FTS triggers drop their entries."""
    await conn.execute("DELETE FROM code_symbols WHERE file_id = ?", (file_id,))
    await conn.execute("DELETE FROM code_chunks WHERE file_id = ?", (file_id,))
    await conn.execute("DELETE FROM code_files WHERE id = ?", (file_id,))


async def _delete_workspace_rows(conn: aiosqlite.Connection, workspace: str) -> None:
    await conn.execute(
        "DELETE FROM code_symbols WHERE file_id IN (SELECT id FROM code_files WHERE workspace = ?)",
        (workspace,),
    )
    await conn.execute(
        "DELETE FROM code_chunks WHERE file_id IN (SELECT id FROM code_files WHERE workspace = ?)",
        (workspace,),
    )
    await conn.execute("DELETE FROM code_files WHERE workspace = ?", (workspace,))


async def _insert_files(
    conn: aiosqlite.Connection,
    workspace: str,
    root: Path,
    files: Sequence[ScannedFile],
    indexed_at: int,
) -> None:
    """Index ``files``: read, parse, insert the file row plus its symbols/chunks.

    A file that cannot be decoded as text (NUL byte, unreadable) is skipped
    without a row, so the next sweep retries it.
    """
    for scanned in files:
        source = read_source(root / scanned.path)
        if source is None:
            continue
        cursor = await conn.execute(
            "INSERT INTO code_files(workspace, path, lang, mtime_ms, size, sha1, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                workspace,
                scanned.path,
                scanned.lang,
                scanned.mtime_ms,
                scanned.size,
                source.sha1,
                indexed_at,
            ),
        )
        file_id = cursor.lastrowid
        await cursor.close()
        parsed = extract(scanned.lang, source.text)
        if parsed.symbols:
            await conn.executemany(
                "INSERT INTO code_symbols(file_id, kind, name, qualname, start_line, end_line, signature, doc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (file_id, s.kind, s.name, s.qualname, s.start_line, s.end_line, s.signature, s.doc)
                    for s in parsed.symbols
                ],
            )
        if parsed.chunks:
            await conn.executemany(
                "INSERT INTO code_chunks(file_id, start_line, end_line, text) VALUES (?, ?, ?, ?)",
                [(file_id, c.start_line, c.end_line, c.text) for c in parsed.chunks],
            )


async def _counts(conn: aiosqlite.Connection, workspace: str) -> tuple[int, int, int]:
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM code_files WHERE workspace = ?", (workspace,)
    ) as cur:
        files = (await cur.fetchone())["n"]
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM code_symbols WHERE file_id IN "
        "(SELECT id FROM code_files WHERE workspace = ?)",
        (workspace,),
    ) as cur:
        symbols = (await cur.fetchone())["n"]
    async with conn.execute(
        "SELECT COUNT(*) AS n FROM code_chunks WHERE file_id IN "
        "(SELECT id FROM code_files WHERE workspace = ?)",
        (workspace,),
    ) as cur:
        chunks = (await cur.fetchone())["n"]
    return files, symbols, chunks


# ── public build entry point ───────────────────────────────────────────────


async def ensure_indexed(workspace: Path, *, force: bool = False) -> IndexStats:
    """Make the index for ``workspace`` current and report what it holds.

    Lazy first build when the workspace has no rows (a deleted database file
    counts as "no rows"), then a cheap sweep on every call: every indexed file
    is ``stat``-ed, changed and new files are re-parsed, vanished files are
    dropped. More than :data:`MAX_INCREMENTAL_CHANGES` changed files, a schema
    version mismatch or ``force=True`` rebuild the workspace from scratch.

    Concurrent calls for the same workspace serialise on one lock, so the index
    is built exactly once and both callers see the finished result.
    """
    started = time.perf_counter()
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"workspace is not a directory: {root}")
    key = str(root)
    # Lazy maintenance, once per process: the first build also drops workspaces
    # whose folder has disappeared. This workspace is passed as ``skip`` because
    # its lock is not held yet; a workspace with a build in flight is skipped
    # inside the sweep. Maintenance failures must not break the requested build.
    try:
        await prune_stale_workspaces(skip=key)
    except Exception as exc:
        logger.warning("code_index: stale-workspace sweep failed: %s", exc)
    lock = await _workspace_lock(key)
    async with lock:
        async with connect() as conn:
            meta = await _meta(conn)
            last_build = meta.get(f"last_build:{key}")
            full = force or not last_build or meta.get("schema_version") != SCHEMA_VERSION

            scanned = list(iter_files(root))
            existing = await _existing_files(conn, key)
            changed: list[ScannedFile] = []
            vanished: list[str] = []
            if not full:
                for scanned_file in scanned:
                    row = existing.get(scanned_file.path)
                    if row is None or row["mtime_ms"] != scanned_file.mtime_ms or row["size"] != scanned_file.size:
                        changed.append(scanned_file)
                seen = {scanned_file.path for scanned_file in scanned}
                vanished = [path for path in existing if path not in seen]
                if len(changed) > MAX_INCREMENTAL_CHANGES:
                    full = True

            indexed_at = _now_ns()
            if full:
                await _delete_workspace_rows(conn, key)
                await _insert_files(conn, key, root, scanned, indexed_at)
            else:
                for path in vanished:
                    await _delete_file_rows(conn, existing[path]["id"])
                for scanned_file in changed:
                    await _delete_file_rows(conn, existing[scanned_file.path]["id"])
                await _insert_files(conn, key, root, changed, indexed_at)

            if full or changed or vanished:
                built_at = _now_iso()
            else:
                built_at = last_build or _now_iso()
            await _set_meta(conn, "schema_version", SCHEMA_VERSION)
            await _set_meta(conn, f"last_build:{key}", built_at)
            await conn.commit()
            files, symbols, chunks = await _counts(conn, key)

    return IndexStats(
        files=files,
        symbols=symbols,
        chunks=chunks,
        built_at=built_at,
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        rebuilt=full,
    )


# ── query surface for the search tool (task 2) ─────────────────────────────


def fts_query(tokens: Sequence[str], *, prefix: bool = False) -> str:
    """Build a safe FTS5 expression: tokens quoted, joined with ``OR``.

    ``prefix=True`` makes each token a prefix query (``"regis"*``), which the
    ranking layer uses for name lookups. Empty tokens are dropped; a result of
    ``""`` must not be passed to ``MATCH``.
    """
    terms: list[str] = []
    for raw in tokens:
        token = " ".join((raw or "").split())
        if not token:
            continue
        terms.append(f'"{token.replace(chr(34), chr(34) * 2)}"' + ("*" if prefix else ""))
    return " OR ".join(terms)


def _like_prefix(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


async def iter_matches(
    workspace: Path,
    tokens: Sequence[str],
    *,
    kinds: Sequence[str] | None = None,
    path_prefix: str | None = None,
    prefix: bool = False,
    limit: int | None = None,
) -> list[Match]:
    """Return FTS hits for ``tokens`` in ``workspace``, best ``bm25`` first.

    Looks in both FTS tables: symbol hits (``source="symbol"``) and text chunk
    hits (``source="text"``). ``kinds`` filters symbol kinds (ignored for chunk
    hits, which are kind ``"text"``); ``path_prefix`` filters on the
    workspace-relative path; ``prefix=True`` turns every token into a prefix
    query (``"regis"*``), which the ranking layer uses for name lookups.
    ``limit`` caps each table's scan, not the final list. Ranking is
    deliberately left to the caller; this returns raw rows plus their ``bm25``
    score. Does not build the index — call :func:`ensure_indexed` first.
    """
    query = fts_query(tokens, prefix=prefix)
    if not query:
        return []
    key = str(Path(workspace).expanduser().resolve())
    matches: list[Match] = []
    async with connect() as conn:
        symbol_sql = [
            "SELECT f.path, s.kind, s.name, s.qualname, s.start_line, s.end_line, s.signature, s.doc,",
            "       bm25(code_symbols_fts) AS score",
            "FROM code_symbols_fts",
            "JOIN code_symbols s ON s.id = code_symbols_fts.rowid",
            "JOIN code_files f ON f.id = s.file_id",
            "WHERE code_symbols_fts MATCH ? AND f.workspace = ?",
        ]
        symbol_params: list[object] = [query, key]
        if kinds:
            symbol_sql.append(f"AND s.kind IN ({', '.join('?' for _ in kinds)})")
            symbol_params.extend(kinds)
        if path_prefix:
            symbol_sql.append("AND f.path LIKE ? ESCAPE '\\'")
            symbol_params.append(_like_prefix(path_prefix))
        symbol_sql.append("ORDER BY score")
        if limit:
            symbol_sql.append("LIMIT ?")
            symbol_params.append(limit)
        async with conn.execute(" ".join(symbol_sql), symbol_params) as cur:
            for row in await cur.fetchall():
                matches.append(Match(
                    source="symbol",
                    path=row["path"],
                    kind=row["kind"],
                    name=row["name"],
                    qualname=row["qualname"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    signature=row["signature"],
                    doc=row["doc"],
                    snippet="",
                    score=row["score"],
                ))

        chunk_sql = [
            "SELECT f.path, c.start_line, c.end_line,",
            "       snippet(code_chunks_fts, 0, '', '', '…', 12) AS snippet,",
            "       bm25(code_chunks_fts) AS score",
            "FROM code_chunks_fts",
            "JOIN code_chunks c ON c.id = code_chunks_fts.rowid",
            "JOIN code_files f ON f.id = c.file_id",
            "WHERE code_chunks_fts MATCH ? AND f.workspace = ?",
        ]
        chunk_params: list[object] = [query, key]
        if path_prefix:
            chunk_sql.append("AND f.path LIKE ? ESCAPE '\\'")
            chunk_params.append(_like_prefix(path_prefix))
        chunk_sql.append("ORDER BY score")
        if limit:
            chunk_sql.append("LIMIT ?")
            chunk_params.append(limit)
        async with conn.execute(" ".join(chunk_sql), chunk_params) as cur:
            for row in await cur.fetchall():
                matches.append(Match(
                    source="text",
                    path=row["path"],
                    kind="text",
                    name="",
                    qualname="",
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    signature="",
                    doc="",
                    snippet=row["snippet"],
                    score=row["score"],
                ))

    matches.sort(key=lambda match: match.score)
    return matches

"""Semantic (symbol + full-text) index over a workspace.

Internal API only: task 2 wraps this in a ``code_search`` tool. The surface is
deliberately small.

* :func:`ensure_indexed` — lazy first build, then a cheap staleness sweep on every
  call. Returns an :class:`IndexStats`.
* :func:`iter_matches` — raw FTS hits (symbols and text chunks) with their
  ``bm25`` score, for the ranking layer to blend. Call :func:`ensure_indexed`
  first; it does not build.
* :func:`connect` — escape hatch for ad-hoc SQL over the same database
  (``async with connect() as conn:``).
* :func:`db_path` — the database file in use (``~/.crabagent/code_index.db`` by
  default; tests monkeypatch :data:`crabagent.core.code_index.store.DB_PATH`).
* :func:`fts_query` — turn user tokens into a safe FTS5 ``MATCH`` expression.

Example::

    stats = await ensure_indexed(Path("/repo"))
    hits = await iter_matches(Path("/repo"), ["tool", "registry"], kinds=["class"])
"""
from __future__ import annotations

from .store import (
    IndexStats,
    Match,
    connect,
    db_path,
    ensure_indexed,
    fts_query,
    iter_matches,
)

__all__ = [
    "IndexStats",
    "Match",
    "connect",
    "db_path",
    "ensure_indexed",
    "fts_query",
    "iter_matches",
]

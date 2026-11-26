"""Tests for molt snapshot store."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core.database import Base
from crabagent.core.molt import store as molt_store


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_molt_inserts_row(db):
    result = await molt_store.create_molt(
        db,
        molt_id="molt_0001",
        session_id="sess1",
        branch_id="main",
        description="test snapshot",
        method="git",
        file_count=3,
    )

    assert result["molt_id"] == "molt_0001"
    assert result["description"] == "test snapshot"
    assert result["file_count"] == 3


@pytest.mark.asyncio
async def test_list_molts_returns_sorted(db):
    await molt_store.create_molt(db, molt_id="molt_0002", session_id="s1", branch_id="main", description="second", method="git", file_count=1)
    await molt_store.create_molt(db, molt_id="molt_0001", session_id="s1", branch_id="main", description="first", method="git", file_count=1)

    molts = await molt_store.list_molts(db, session_id="s1")

    assert len(molts) >= 2
    ids = [m["molt_id"] for m in molts]
    assert "molt_0001" in ids
    assert "molt_0002" in ids


def test_molt_dir_uses_workspace(tmp_path: Path):
    result = molt_store.molt_dir(tmp_path)
    assert result == tmp_path / ".crabagent" / "molts"


def test_snapshot_path_builds_correctly(tmp_path: Path):
    result = molt_store.snapshot_path("molt_001", "src/app.py", tmp_path)
    assert "molt_001" in str(result)
    assert "src/app.py" in str(result)


@pytest.mark.asyncio
async def test_list_molt_files_returns_empty_for_nonexistent(tmp_path: Path):
    result = await molt_store.list_molt_files("nonexistent", workspace=tmp_path)
    assert isinstance(result, list)


def test_get_snapshot_content_returns_empty_for_missing(tmp_path: Path):
    result = molt_store.get_snapshot_content("nonexistent", "file.py", workspace=tmp_path)
    assert result == ""


def test_get_current_content_returns_empty_for_missing(tmp_path: Path):
    result = molt_store.get_current_content(tmp_path, "nonexistent.py")
    assert result == ""


def test_get_current_content_reads_file(tmp_path: Path):
    f = tmp_path / "test.py"
    f.write_text("hello world")
    result = molt_store.get_current_content(tmp_path, "test.py")
    assert result == "hello world"


# ── staging cleanup (audit batch 4, item 21) ─────────────────────────


def _batch(tmp_path: Path, name: str, age_seconds: float = 7200) -> tuple[Path, Path]:
    """Create ``_staging/<name>`` holding one staged file, aged past the window."""
    md_base = tmp_path / ".crabagent" / "molts"
    batch = md_base / "_staging" / name
    batch.mkdir(parents=True)
    (batch / "staged.py").write_text("before\n", encoding="utf-8")
    stamp = time.time() - age_seconds
    os.utime(batch, (stamp, stamp))
    return md_base, batch


def _write_marker(batch: Path, pid: int) -> None:
    (batch / molt_store._STAGING_MARKER).write_text(
        json.dumps({"pid": pid, "created_at": time.time()}), encoding="utf-8"
    )


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=60)
    return proc.pid


def test_live_staging_batch_survives_prune_however_old(tmp_path: Path):
    """A pending confirmation can outlast the hour: the owner pid decides."""
    md_base, batch = _batch(tmp_path, "live")
    molt_store.mark_staging_owner(batch)
    stamp = time.time() - 7200  # marker write would otherwise refresh mtime
    os.utime(batch, (stamp, stamp))

    assert molt_store._prune_stale_staging(md_base) == 0
    assert batch.is_dir()
    assert (batch / "staged.py").read_text(encoding="utf-8") == "before\n"


def test_staging_batch_with_dead_owner_is_pruned(tmp_path: Path):
    md_base, batch = _batch(tmp_path, "dead")
    _write_marker(batch, _dead_pid())
    stamp = time.time() - 7200
    os.utime(batch, (stamp, stamp))

    assert molt_store._prune_stale_staging(md_base) == 1
    assert not batch.exists()


def test_staging_batch_without_marker_falls_back_to_age(tmp_path: Path):
    """Older batches carry no marker: age still cleans them up."""
    md_base, batch = _batch(tmp_path, "unmarked")

    assert molt_store._prune_stale_staging(md_base) == 1
    assert not batch.exists()


def test_recent_staging_batch_is_never_pruned(tmp_path: Path):
    md_base, batch = _batch(tmp_path, "fresh", age_seconds=0)

    assert molt_store._prune_stale_staging(md_base) == 0
    assert batch.is_dir()


@pytest.mark.asyncio
async def test_prune_keeps_staging_and_drops_orphan_molts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import crabagent.core.database as database

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'prune.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)

    md_base, batch = _batch(tmp_path, "in-flight", age_seconds=0)
    orphan = md_base / "molt_orphan"
    orphan.mkdir()
    (orphan / "old.py").write_text("gone\n", encoding="utf-8")
    try:
        assert await molt_store.prune_molts(tmp_path) == 1
    finally:
        await engine.dispose()

    assert not orphan.exists()
    assert batch.is_dir(), "the orphan scan must never treat _staging as an orphan"
    assert (batch / "staged.py").exists()

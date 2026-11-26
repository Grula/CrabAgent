from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crabagent.core.config import settings
from crabagent.core.database import Molt
from crabagent.core.timeutil import iso_utc

logger = logging.getLogger(__name__)

# A staging batch is only alive for the duration of one agent round or editor
# save, so anything untouched for this long was left by a crashed session.
_STAGING_STALE_SECONDS = 3600

# Written into every batch dir at creation. Age alone cannot tell a crashed
# batch from one still waiting on a tool call or a user confirmation, so the
# owning pid decides: a live batch is never pruned however old it gets.
_STAGING_MARKER = ".staging_owner.json"


_SELF_IGNORE = "# Created by CrabAgent: edit snapshots (molts), never part of the project.\n*\n"


def ensure_molt_root(molts: Path) -> None:
    """Make ``<workspace>/.crabagent/molts`` ignore itself in git (like .pytest_cache).

    Snapshots live inside the user's workspace; without this they show up in
    ``git status`` and in every agent's diff and scope check as new files.
    """
    try:
        molts.mkdir(parents=True, exist_ok=True)
        marker = molts / ".gitignore"
        if not marker.exists():
            marker.write_text(_SELF_IGNORE, encoding="utf-8")
    except OSError:
        logger.debug("Could not write %s/.gitignore", molts, exc_info=True)


def mark_staging_owner(staging: Path) -> None:
    """Record the owning process inside a staging batch directory."""
    import json
    import os
    import time

    try:
        ensure_molt_root(staging.parent.parent)
        staging.mkdir(parents=True, exist_ok=True)
        (staging / _STAGING_MARKER).write_text(
            json.dumps({"pid": os.getpid(), "created_at": time.time()}), encoding="utf-8"
        )
    except OSError:
        logger.debug("Could not write staging marker in %s", staging, exc_info=True)


def _staging_owner_alive(entry: Path) -> bool | None:
    """True/False from the batch marker, or None when it cannot be checked.

    None covers a missing marker (older version), an unreadable one, and
    platforms where a signal-0 probe is not safe: those fall back to age.
    """
    import json
    import os

    if os.name != "posix":  # os.kill(pid, 0) on Windows terminates the process
        return None
    try:
        pid = int(json.loads((entry / _STAGING_MARKER).read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)  # signal 0 only probes existence; nothing is delivered
    except ProcessLookupError:
        return False  # no such process: the batch was abandoned
    except PermissionError:
        return True  # exists but owned by another user: still live
    except OSError:
        return None
    return True


def _prune_stale_staging(md_base: Path) -> int:
    """Remove staging batches left behind by a crashed session.

    Each in-flight batch stages under its own ``_staging/<id>`` directory, so a
    fresh directory belongs to a live write and must survive. A batch that is
    still live (its owning pid answers) survives regardless of age: a pending
    confirmation can outlast the hour, and pruning it would destroy the snapshot
    its later flush needs. Everything else is removed once untouched for an
    hour.
    """
    staging = md_base / "_staging"
    if not staging.is_dir():
        return 0
    import shutil
    import time

    cutoff = time.time() - _STAGING_STALE_SECONDS
    pruned = 0
    for entry in staging.iterdir():
        if entry.is_dir() and _staging_owner_alive(entry):
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        if entry.is_dir():
            shutil.rmtree(str(entry), ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        pruned += 1
    try:
        staging.rmdir()  # only when nothing is staging right now
    except OSError:
        pass
    return pruned


def molt_dir(workspace: Path | None = None) -> Path:
    ws = workspace.resolve() if workspace else settings.workspace.resolve()
    return ws / ".crabagent" / "molts"


def snapshot_path(molt_id: str, filepath: str, workspace: Path | None = None) -> Path:
    return molt_dir(workspace) / molt_id / filepath


async def list_molts(db: AsyncSession, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Molt).where(Molt.session_id == session_id).order_by(Molt.created_at.desc()).limit(limit)
    )
    return [_molt_to_dict(m) for m in result.scalars().all()]


def _molt_to_dict(m: Molt) -> dict[str, Any]:
    return {
        "molt_id": m.molt_id,
        "session_id": m.session_id,
        "branch_id": m.branch_id,
        "description": m.description,
        "method": m.method,
        "file_count": m.file_count,
        "workspace": m.workspace or "",
        "created_at": iso_utc(m.created_at),
    }


async def create_molt(
    db: AsyncSession,
    molt_id: str,
    session_id: str,
    branch_id: str,
    description: str,
    method: str,
    file_count: int,
    workspace: str = "",
) -> dict[str, Any]:
    molt = Molt(
        molt_id=molt_id,
        session_id=session_id,
        branch_id=branch_id,
        description=description,
        method=method,
        file_count=file_count,
        workspace=workspace,
    )
    db.add(molt)
    await db.commit()
    await db.refresh(molt)
    return _molt_to_dict(molt)


async def list_molt_files(molt_id: str, workspace: Path | None = None) -> list[str]:
    md = molt_dir(workspace) / molt_id
    if not md.exists():
        return []
    files = []
    for f in sorted(md.rglob("*")):
        if f.is_file():
            rel = f.relative_to(md)
            files.append(str(rel))
    return files


def get_snapshot_content(molt_id: str, filepath: str, workspace: Path | None = None) -> str:
    p = snapshot_path(molt_id, filepath, workspace)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def get_current_content(workspace: Path, filepath: str) -> str:
    p = workspace / filepath
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


async def prune_molts(workspace: Path | None = None) -> int:
    import shutil

    from crabagent.core.database import async_session_factory

    keep = settings.molt_keep_count
    pruned = 0
    md_base = molt_dir(workspace)
    db_ids: set[str] = set()

    # Clean up staging left by a crashed session, but never a batch that is
    # still in flight (and especially never the whole shared _staging dir).
    pruned += _prune_stale_staging(md_base)

    async with async_session_factory() as db:
        # Prune old molts for THIS workspace only
        ws_str = str(md_base.parent.parent.resolve()) if md_base.exists() else ""
        stmt = select(Molt).order_by(Molt.created_at.desc())
        if ws_str:
            stmt = stmt.where(Molt.workspace == ws_str)
        result = await db.execute(stmt.offset(keep))
        old_molts = result.scalars().all()
        for m in old_molts:
            md = md_base / m.molt_id
            if md.exists():
                shutil.rmtree(str(md))
            await db.delete(m)
            pruned += 1
        if pruned:
            await db.commit()
            logger.info("Pruned %d old molts", pruned)

        # Get all remaining DB molt IDs for orphan detection
        if ws_str:
            result2 = await db.execute(select(Molt.molt_id).where(Molt.workspace == ws_str))
        else:
            result2 = await db.execute(select(Molt.molt_id))
        db_ids = {row[0] for row in result2.fetchall()}

    # Clean up orphaned directories (on disk but not in DB). `_staging` is not a
    # molt but the live scratch area for in-flight writes: never an orphan.
    if md_base.exists():
        orphan_count = 0
        for d in md_base.iterdir():
            if d.is_dir() and d.name != "_staging" and d.name not in db_ids:
                shutil.rmtree(str(d), ignore_errors=True)
                orphan_count += 1
        if orphan_count:
            logger.info("Cleaned up %d orphaned molt directories", orphan_count)
            pruned += orphan_count

    return pruned

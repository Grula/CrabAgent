from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

_file_locks: dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()


def file_lock(path: Path) -> threading.Lock:
    """One lock per file, held across a tool's read-modify-write.

    Tools in one batch run in parallel threads. Without this, two edits to the
    same file both read the original and the second write erases the first.
    """
    key = str(Path(path).resolve())
    with _file_locks_guard:
        return _file_locks.setdefault(key, threading.Lock())


def get_workspace_root(context: Any = None) -> Path | None:
    if context is None or not hasattr(context, "workspace") or not context.workspace:
        return None
    try:
        return Path(context.workspace).resolve()
    except Exception:
        return None


def resolve_tool_path(file_path: str, context: Any = None) -> tuple[Path | None, str | None]:
    """Resolve a tool path, using workspace as the base for relative paths."""
    try:
        raw_path = Path(file_path).expanduser()
    except Exception as e:
        return None, f"Error: invalid path '{file_path}': {e}"

    workspace = get_workspace_root(context)
    try:
        if workspace is not None and not raw_path.is_absolute():
            return (workspace / raw_path).resolve(), None
        return raw_path.resolve(), None
    except Exception as e:
        return None, f"Error: invalid path '{file_path}': {e}"

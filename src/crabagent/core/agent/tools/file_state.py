"""What an agent has read, so edits are made against the file it actually saw.

``read`` records a content hash for each file it returns;
``edit`` and ``write`` record the hash of what they wrote. Before changing an
existing file, ``edit`` and ``write`` ask :meth:`FileStateTracker.check`:

* never read in this run (or session): refuse, so ``old_string`` comes from the
  file's real content and not from memory or a ``sed`` printout;
* changed on disk since (another agent, a command, the user's editor): refuse,
  so a stale view never overwrites someone else's change.

Content hashes decide, so a touch that leaves the bytes unchanged is not a
change. Parallel sub-agents each have their own tracker, which
is what makes one coder notice another's edit to a shared file.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any


def _digest(path: Path, data: bytes | None = None) -> str:
    return hashlib.sha1(path.read_bytes() if data is None else data).hexdigest()


class FileStateTracker:
    """Per-run (or per-chat-session) record of the files an agent has seen."""

    def __init__(self) -> None:
        # Content hash of each file as this agent last saw it. Content, not mtime:
        # two same-size writes within one filesystem timestamp tick (a few ms)
        # have identical mtime and size, and edit reads the whole file anyway.
        self._stamps: dict[str, str] = {}
        # Files this agent changed with edit/write, in order (dict as ordered set).
        self._written: dict[str, None] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(path: Path) -> str:
        return str(Path(path).resolve())

    def record(self, path: Path, data: bytes | None = None, *, written: bool = False) -> None:
        """Remember *path* as seen now. ``data`` is its content when already read;
        ``written`` marks a file this agent changed (see :meth:`written_paths`)."""
        try:
            stamp = _digest(Path(path), data)
        except OSError:
            return
        key = self._key(path)
        with self._lock:
            self._stamps[key] = stamp
            if written:
                self._written.pop(key, None)
                self._written[key] = None

    def forget(self, path: Path) -> None:
        with self._lock:
            self._stamps.pop(self._key(path), None)

    def seen(self, path: Path) -> bool:
        with self._lock:
            return self._key(path) in self._stamps

    def written_paths(self) -> list[str]:
        """Absolute paths of the files changed with edit/write, least recent first."""
        with self._lock:
            return list(self._written)

    def check(self, path: Path, display: str, action: str = "editing") -> str | None:
        """An error message when *path* may not be changed yet, else ``None``.

        Only existing files are checked; creating a new file needs no read.
        """
        path = Path(path)
        if not path.exists():
            return None
        key = self._key(path)
        with self._lock:
            known = self._stamps.get(key)
        if known is None:
            return (
                f"Error: read {display} with the read tool before {action} it, so the change is based on its "
                "current content (a bash printout doesn't count). Then retry."
            )
        try:
            current = _digest(path)
        except OSError:
            return None
        if current == known:
            return None
        return (
            f"Error: {display} changed on disk since you last read it (another agent, a command or the user "
            "changed it). Read it again and redo the change against what is there now."
        )


def tracker_of(context: Any) -> FileStateTracker | None:
    """The tracker of a run context, or ``None`` (direct calls, tests): no checks then."""
    tracker = getattr(context, "file_state", None)
    return tracker if isinstance(tracker, FileStateTracker) else None


# Chat sessions build a fresh context for every turn; the session's tracker
# lives here so a file read in one turn can be edited in the next.
_SESSION_TRACKERS: OrderedDict[str, FileStateTracker] = OrderedDict()
_SESSION_TRACKERS_MAX = 64
_SESSION_LOCK = threading.Lock()


def session_tracker(session_id: str) -> FileStateTracker:
    """The tracker shared by every turn of one chat session (LRU-bounded)."""
    with _SESSION_LOCK:
        tracker = _SESSION_TRACKERS.get(session_id)
        if tracker is None:
            tracker = FileStateTracker()
            _SESSION_TRACKERS[session_id] = tracker
            while len(_SESSION_TRACKERS) > _SESSION_TRACKERS_MAX:
                _SESSION_TRACKERS.popitem(last=False)
        else:
            _SESSION_TRACKERS.move_to_end(session_id)
        return tracker

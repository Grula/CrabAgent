"""A small, size-capped tree of the workspace for the system prompt.

The model should know the shape of the project without spending a tool call on
``glob``. The tree is deterministic (sorted, directories before files), depth
capped, entry capped and hard capped in characters, so a pathological workspace
cannot blow the prompt budget.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

WORKSPACE_SECTION_HEADING = "## Workspace"

DEFAULT_MAX_ENTRIES = 200
DEFAULT_MAX_CHARS = 4000
DEFAULT_MAX_DEPTH = 3

# Noise a code agent never needs to see listed.
SKIP_DIR_NAMES = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "site-packages",
})


# Upper bound on how many further entries we walk just to report "N more", so
# counting the tail of a huge tree stays cheap.
COUNT_VISIT_CAP = 5000

_INDENT = "  "


def skip_entry(name: str, is_dir: bool) -> bool:
    """True for noise the agent never needs: hidden entries (``.git``,
    ``.crabagent``, ``.env``, ...) and the build/dependency/cache directories in
    :data:`SKIP_DIR_NAMES`. Public so other walkers (the code index) use the
    same rule instead of a copy."""
    if is_dir and name in SKIP_DIR_NAMES:
        return True
    return name.startswith(".")


def _children(path: Path) -> tuple[list[str], list[str]]:
    """Return (dirs, files) for ``path``, sorted, directories first. Never raises."""
    dirs: list[str] = []
    files: list[str] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    # follow_symlinks=False: a symlinked directory is a leaf, so
                    # the walk cannot loop.
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if skip_entry(entry.name, is_dir):
                    continue
                (dirs if is_dir else files).append(entry.name)
    except OSError:
        return [], []
    return sorted(dirs), sorted(files)


def _iter_entries(path: Path, depth: int, max_depth: int) -> Iterator[tuple[int, str, bool]]:
    """Yield ``(depth, name, is_dir)`` as a tree: directories (sorted) before
    files at each level, each directory followed by its own children."""
    dirs, files = _children(path)
    for name in dirs:
        yield depth, name, True
        if depth + 1 < max_depth:
            yield from _iter_entries(path / name, depth + 1, max_depth)
    for name in files:
        yield depth, name, False


def build_workspace_tree(
    workspace: Path,
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> str:
    """Render a compact tree of ``workspace``, or ``""`` if it cannot be read.

    Directories are suffixed with ``/``; one level of nesting adds two spaces of
    indentation. When an entry or character cap is hit the listing stops and a
    ``… (N more entries not shown)`` line is appended.
    """
    if max_entries <= 0 or max_chars <= 0:
        return ""
    try:
        root = Path(workspace)
        if not root.is_dir():
            return ""

        lines: list[str] = []
        total = 0
        shown = 0
        entries = _iter_entries(root, 0, max_depth)
        truncated = False
        for depth, name, is_dir in entries:
            line = f"{_INDENT * depth}{name}{'/' if is_dir else ''}"
            if shown >= max_entries or total + len(line) + 1 > max_chars:
                truncated = True
                break
            lines.append(line)
            total += len(line) + 1
            shown += 1

        # Count what was left over, so the footer is measured, not guessed.
        # ``truncated`` means the entry that hit the cap was consumed unshown.
        more = 1 if truncated else 0
        for _depth, _name, _is_dir in entries:
            more += 1
            if more >= COUNT_VISIT_CAP:
                break

        if more:
            # Re-format on every trim: ``more`` grows as lines are dropped, and a
            # footer formatted once before the loop would keep reporting the
            # smaller pre-trim number (and its shorter, stale length).
            while True:
                footer = f"… ({more} more entries not shown)"
                if total + len(footer) + 1 <= max_chars:
                    break
                if not lines:
                    return ""
                total -= len(lines.pop()) + 1
                more += 1
            lines.append(footer)

        return "\n".join(lines)
    except Exception:  # noqa: BLE001 - a listing must never break a run
        return ""


def build_workspace_section(
    workspace: Path | None,
    *,
    max_entries: int | None = None,
) -> str:
    """Return the ``## Workspace`` prompt section, or ``""`` when disabled/empty."""
    from crabagent.core.config import settings

    cap = settings.workspace_tree_max_entries if max_entries is None else max_entries
    if cap <= 0 or workspace is None:
        return ""
    tree = build_workspace_tree(workspace, max_entries=cap)
    if not tree:
        return ""
    return f"{WORKSPACE_SECTION_HEADING}\n{tree}"

"""Preview the change a mutating tool is about to make.

When a tool call needs the user's confirmation, the confirmation card shows
only the tool name and its arguments. For ``write``/``edit`` that is not enough
to judge the call, so this module renders the pending change as a unified diff
that the UI can display next to the confirm buttons.

The public entry point is :func:`build_diff_preview`. It is a coroutine and is
deliberately tolerant: it never raises, it returns ``None`` whenever a preview
cannot be produced honestly (unknown tool, unreadable/binary/oversized file, an
``edit`` whose ``old_string`` is not in the file, input too large to diff in
time), and it never touches the disk for tools that do not preview a change.

Payload shape (stable contract, consumed by the web UI)::

    {
        "path": "src/app.py",       # workspace-relative, POSIX separators
        "unified": "--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1,3 +1,3 @@\\n...",
        "added": 3,                  # changed-line counts of the whole diff
        "removed": 1,
        "truncated": False,          # True when the text hit a size cap
        "new_file": False            # True when the file does not exist yet
    }

``unified`` has no timestamps and no trailing newline. When ``truncated`` is
True the text ends with the marker line ``... [diff truncated]``.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
from pathlib import Path
from typing import Any

from .path_utils import resolve_tool_path

logger = logging.getLogger(__name__)

# Tools whose args are rendered as a diff. Everything else returns None so a
# confirmation for bash/browser/etc. costs no file I/O.
_PREVIEW_TOOLS = frozenset({"write", "edit"})

# Input caps: a diff of a huge or binary blob is useless (and slow) in a card.
_MAX_FILE_BYTES = 1_000_000  # 1 MB
_BINARY_PROBE_BYTES = 4096

# Skip before computing. ``difflib`` is quadratic in the worst case: an
# interleaved rewrite of 10k lines took 11 s here (4.4 s in the audit) while the
# card shows at most _MAX_DIFF_LINES lines. 4000 lines is ten times what the
# card displays and already far above any file a human reviews in it, so above
# that the confirmation gets no preview rather than a slow one. The character
# cap covers the other shape: a few very long lines keep the line count small
# but cost the same.
_MAX_DIFF_INPUT_LINES = 4000
_MAX_DIFF_INPUT_CHARS = 1_000_000

# Hard wall-clock bound for the diff itself. It runs in a worker thread, so a
# timeout cannot stop the computation, but it does stop the confirmation from
# waiting on it: over budget means no preview, never an error.
_DIFF_TIME_BUDGET_SECONDS = 2.0

# Output caps: keep the card (and the event payload) small.
_MAX_DIFF_LINES = 400
_MAX_DIFF_CHARS = 100_000
_TRUNCATION_MARKER = "... [diff truncated]"

_CONTEXT_LINES = 3


class _Unpreviewable(Exception):
    """The file cannot be rendered as a text diff (too large, binary, ...)."""


class _WorkspaceContext:
    """Just enough of an AgentContext for :func:`resolve_tool_path`."""

    __slots__ = ("workspace",)

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace


def _resolve_target(workspace: Path | str | None, raw_path: str) -> tuple[Path, str]:
    """Resolve *raw_path* inside *workspace*; raise _Unpreviewable when it escapes.

    Resolution goes through the tools' own path helper, so the preview looks at
    the file the tool will really touch: ``write("~/x")`` must not be shown as a
    new file while the tool overwrites the existing ``~/x``.
    """
    if workspace is None or not raw_path:
        raise _Unpreviewable("no workspace or path")
    try:
        ws = Path(workspace).resolve()
    except (OSError, RuntimeError):
        raise _Unpreviewable("workspace cannot be resolved") from None
    target, error = resolve_tool_path(raw_path, _WorkspaceContext(ws))
    if error or target is None:
        raise _Unpreviewable(error or "path cannot be resolved")
    try:
        rel = target.relative_to(ws).as_posix()
    except ValueError:
        raise _Unpreviewable("path is outside the workspace") from None
    return target, rel


def _read_text(target: Path) -> str:
    """Read a text file, refusing oversized or binary files."""
    if target.stat().st_size > _MAX_FILE_BYTES:
        raise _Unpreviewable("file is larger than 1 MB")
    with target.open("rb") as fh:
        head = fh.read(_BINARY_PROBE_BYTES)
        if b"\x00" in head:
            raise _Unpreviewable("file looks binary")
        data = head + fh.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise _Unpreviewable("file is not utf-8 text") from None


def _make_diff(old_text: str, new_text: str, rel: str) -> tuple[str, int, int, bool]:
    """Return ``(unified, added, removed, truncated)`` for one file."""
    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=_CONTEXT_LINES,
            lineterm="",
        )
    )
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

    truncated = False
    if len(diff_lines) > _MAX_DIFF_LINES:
        diff_lines = diff_lines[:_MAX_DIFF_LINES]
        truncated = True
    unified = "\n".join(diff_lines)
    if len(unified) > _MAX_DIFF_CHARS:
        unified = unified[:_MAX_DIFF_CHARS]
        truncated = True
    if truncated:
        unified = f"{unified}\n{_TRUNCATION_MARKER}"
    return unified, added, removed, truncated


async def build_diff_preview(
    tool_name: str, args: dict[str, Any] | None, workspace: Path | str | None
) -> dict[str, Any] | None:
    """Render the pending change of a ``write``/``edit`` call as a unified diff.

    Returns the payload described in the module docstring, or ``None`` when no
    honest preview exists. Never raises.

    The diff itself is quadratic in the worst case, so it runs in a worker
    thread under a wall-clock budget: a confirmation must never freeze the
    event loop (and with it every SSE stream) for seconds.
    """
    try:
        prepared = _prepare(tool_name, args or {}, workspace)
        if prepared is None:
            return None
        old_text, new_text, rel, exists = prepared
        unified, added, removed, truncated = await asyncio.wait_for(
            asyncio.to_thread(_make_diff, old_text, new_text, rel),
            timeout=_DIFF_TIME_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.debug(
            "diff preview for %s exceeded the %.1fs budget; no preview",
            tool_name,
            _DIFF_TIME_BUDGET_SECONDS,
        )
        return None
    except Exception:
        logger.debug("diff preview failed for %s", tool_name, exc_info=True)
        return None

    if not unified:
        return None
    return {
        "path": rel,
        "unified": unified,
        "added": added,
        "removed": removed,
        "truncated": truncated,
        "new_file": not exists,
    }


def _too_large_to_diff(old_text: str, new_text: str) -> bool:
    """True when computing the diff would cost more than the card is worth."""
    if max(len(old_text.splitlines()), len(new_text.splitlines())) > _MAX_DIFF_INPUT_LINES:
        return True
    return len(old_text) + len(new_text) > _MAX_DIFF_INPUT_CHARS


def _is_true(value: Any) -> bool:
    """Strict truth check, so a leftover string ``"false"`` is not a replace-all."""
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return value is True


def _edited_text(old_text: str, args: dict[str, Any]) -> str | None:
    """The text ``edit`` would produce for *args*, or None when nothing applies.

    The batch form (``replacements``) is applied with ``edit``'s own helper, so
    the preview shows exactly the content the tool is about to write. A batch is
    all-or-nothing: if any item would fail (not found, or ambiguous without
    ``replace_all``) nothing is written, so no diff is previewed either.
    """
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    replacements = args.get("replacements")
    if (old_string is not None or new_string is not None) and replacements is not None:
        # The tool rejects this combination; do not invent a preview for it.
        return None

    if replacements is not None:
        items = [replacements] if isinstance(replacements, dict) else replacements
        if not isinstance(items, list) or not items:
            return None
        # Imported lazily: edit.py imports registry, which imports this module.
        from .edit import _apply_items, _batch_ok

        new_text, outcomes = _apply_items(old_text, items)
        if not _batch_ok(outcomes):
            # The tool would refuse the whole batch; showing a partial diff
            # would advertise a write that is not going to happen.
            return None
        return new_text

    if not isinstance(old_string, str) or not old_string or not isinstance(new_string, str):
        return None
    if old_string not in old_text:
        # Project "edit" would fail too; do not invent a diff.
        return None
    if _is_true(args.get("replace_all")):
        return old_text.replace(old_string, new_string)
    return old_text.replace(old_string, new_string, 1)


def _prepare(
    tool_name: str, args: dict[str, Any], workspace: Path | str | None
) -> tuple[str, str, str, bool] | None:
    """Cheap part of the preview: ``(old_text, new_text, rel_path, exists)``.

    Runs on the caller's thread; it only reads a file of at most
    _MAX_FILE_BYTES. Returns None when the call cannot be previewed.
    """
    if tool_name not in _PREVIEW_TOOLS:
        return None
    raw_path = args.get("path") or args.get("file_path")
    if not isinstance(raw_path, str):
        return None
    target, rel = _resolve_target(workspace, raw_path)

    try:
        old_text = _read_text(target)
        exists = True
    except FileNotFoundError:
        old_text = ""
        exists = False
    except _Unpreviewable:
        return None

    if tool_name == "write":
        new_text = args.get("content")
        if not isinstance(new_text, str):
            return None
    else:
        if not exists:
            return None
        new_text = _edited_text(old_text, args)
        if new_text is None:
            return None

    if old_text == new_text:
        return None
    if _too_large_to_diff(old_text, new_text):
        # No preview rather than a slow one.
        return None
    return old_text, new_text, rel, exists

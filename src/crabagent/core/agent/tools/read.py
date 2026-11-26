from __future__ import annotations

from typing import Any

from crabagent.core.agent.multimodal import image_file_to_block, is_image_path
from crabagent.core.agent.token_limits import is_vision_model
from crabagent.core.agent.tools.file_state import tracker_of
from crabagent.core.agent.tools.path_utils import file_lock, resolve_tool_path
from crabagent.core.agent.tools.registry import registry
from crabagent.core.agent.tools.text_encoding import decode_file
import codecs

_MAX_BATCH_FILES = 10

# Shared exclude set — keep in sync with glob.py and grep.py
_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    ".venv",
    "venv",
    ".eggs",
    ".egg-info",
    "dist",
    "build",
    "molts",
    ".opencode",
    "site-packages",
    ".npm",
    ".cargo",
}


def _is_binary(filepath: str) -> bool:
    """Quick heuristic: read first 4KB and check for NUL bytes."""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(4096)
        return b"\x00" in chunk and not chunk.startswith((
            codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE,
            codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE,
        ))
    except (OSError, PermissionError):
        return True


@registry.register(
    name="read",
    description=(
        "Read a file or directory from the filesystem. Returns text with line numbers, a directory listing, "
        "or an image inline for direct inspection when the current model supports vision. Prefer this over "
        "a separate vision tool when an image path is already available."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path or workspace-relative path to the file or directory to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed). Default: 1.",
                "default": 1,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read. Default: 2000.",
                "default": 2000,
            },
            "files": {
                "type": "array",
                "description": (
                    f"Read several files or line ranges in one call (at most {_MAX_BATCH_FILES}), instead of "
                    "file_path: each item is {file_path, offset?, limit?}. Use it rather than several reads "
                    "or a bash sed/cat."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["file_path"],
                },
            },
        },
    },
)
def read_file(
    file_path: str | None = None,
    offset: int = 1,
    limit: int = 2000,
    files: list | None = None,
    context: Any = None,
) -> str | list[dict[str, Any]]:
    if files is not None:
        if file_path:
            return "Error: pass either file_path or files, not both."
        return _read_batch(files, context)
    if not file_path:
        return "Error: file_path is required (or files, to read several in one call)."
    return _read_one(file_path, offset, limit, context)


def _read_batch(files: Any, context: Any) -> str:
    if isinstance(files, dict):
        files = [files]
    if not isinstance(files, list) or not files:
        return "Error: files must be a non-empty array of {file_path, offset?, limit?} objects."
    if len(files) > _MAX_BATCH_FILES:
        return f"Error: at most {_MAX_BATCH_FILES} files per call; split the list."
    sections: list[str] = []
    for item in files:
        if not isinstance(item, dict) or not str(item.get("file_path") or item.get("path") or "").strip():
            sections.append("Error: each item needs a file_path.")
            continue
        name = str(item.get("file_path") or item.get("path")).strip()
        try:
            item_offset = int(item.get("offset") or 1)
            item_limit = int(item.get("limit") or 2000)
        except (TypeError, ValueError):
            sections.append(f"Error: offset and limit must be integers ({name}).")
            continue
        result = _read_one(name, item_offset, item_limit, context)
        if isinstance(result, list):
            # Image blocks can't be combined with text sections; describe instead.
            result = f"[Image file: {name}: read it on its own to see the image.]"
        sections.append(result)
    return "\n\n".join(sections)


def _read_one(
    file_path: str,
    offset: int,
    limit: int,
    context: Any,
) -> str | list[dict[str, Any]]:
    path, error = resolve_tool_path(file_path, context)
    if error:
        return error
    assert path is not None
    if not path.exists():
        return f"Error: path does not exist: {file_path}"

    # ── Directory listing ───────────────────────────────────────────────
    if path.is_dir():
        entries = []
        for entry in sorted(path.iterdir()):
            # Only skip known noise directories; show dotfiles (.env, .gitignore, etc.)
            if entry.is_dir() and entry.name in _SKIP_DIRS:
                continue
            suffix = "/" if entry.is_dir() else ""
            entries.append(entry.name + suffix)
        return "\n".join(entries) if entries else "(empty directory)"

    # ── File reading ────────────────────────────────────────────────────
    if is_image_path(path):
        model = getattr(context, "model", "") or ""
        if context is not None:
            model = context.metadata.get("_resolved_model", model)
        if model and is_vision_model(model):
            block = image_file_to_block(path)
            if block:
                return [
                    {"type": "text", "text": f"[Image file: {path} — inspect the attached image directly.]"},
                    block,
                ]
        size = path.stat().st_size
        return (
            f"[Image file: {path} ({size:,} bytes)]\n"
            "The current model cannot receive this image directly; use an image analysis tool if needed."
        )

    # Skip other binary files
    if _is_binary(str(path)):
        size = path.stat().st_size
        return (
            f"[Binary file: {file_path} ({size:,} bytes)]\n"
            f"Binary content not displayed. Use a specialized tool to inspect."
        )

    tracker = tracker_of(context)
    try:
        # Under the file lock: a parallel edit of the same file must not finish
        # between these bytes being read and their hash being recorded.
        with file_lock(path):
            raw = path.read_bytes()
            if tracker is not None:
                tracker.record(path, raw)
        lines = decode_file(raw).splitlines(keepends=True)
    except Exception as e:
        return f"Error reading file: {e}"

    total = len(lines)
    start = max(0, offset - 1)
    end = min(total, start + limit)
    selected = lines[start:end]

    result = []
    for i, line in enumerate(selected, start=start + 1):
        result.append(f"{i}: {line.rstrip()}")

    header = f"[File: {file_path} ({total} lines total)]\n"
    return header + "\n".join(result)

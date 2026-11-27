"""code_nav: go to definition / find references from a position, through the language server.

``code_search kind="reference"`` starts from a name; this starts from where a name
is used ("what is this ``execute`` on line 864?"), which name matching cannot
answer. Python only (basedpyright); for TypeScript use code_search.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from crabagent.core.agent.tools.path_utils import get_workspace_root, resolve_tool_path
from crabagent.core.agent.tools.registry import registry
from crabagent.core.code_index.lsp import navigator_for, utf16_column
from crabagent.core.code_index.references import _is_test_path

DEFAULT_LIMIT = 30
MAX_LIMIT = 100


def _line_text(path: Path, line: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return lines[line - 1] if 0 < line <= len(lines) else ""


@registry.register(
    name="code_nav",
    description=(
        "Go to the definition of, or find every reference to, the Python name used at a given place: a "
        "file, a line and the name as written there. Exact (it asks the Python language server), so "
        "`registry.execute` and `db.execute` are told apart, which name searches can't. Python files only; "
        "for TypeScript use code_search kind=\"reference\"."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["definition", "references"]},
            "path": {"type": "string", "description": "The file the name is used in (workspace-relative)."},
            "line": {"type": "integer", "description": "1-based line number where the name appears."},
            "name": {"type": "string", "description": "The name as written on that line, e.g. execute."},
            "occurrence": {
                "type": "integer",
                "description": "Which occurrence of the name on that line (1 = first). Default 1.",
                "default": 1,
            },
            "limit": {"type": "integer", "description": f"Maximum references to show. Default {DEFAULT_LIMIT}."},
        },
        "required": ["action", "path", "line", "name"],
    },
    metadata={"source": "builtin", "category": "search"},
)
async def code_nav(
    action: str, path: str, line: int, name: str, occurrence: int = 1, limit: int = DEFAULT_LIMIT,
    context: Any = None,
) -> str:
    workspace = get_workspace_root(context)
    if workspace is None:
        return "Error: code_nav requires an active session with a workspace"
    if action not in ("definition", "references"):
        return 'Error: action must be "definition" or "references".'
    target, error = resolve_tool_path(path, context)
    if error:
        return error
    assert target is not None
    if target.suffix.lower() not in (".py", ".pyi"):
        return 'Error: code_nav works on Python files; for other languages use code_search kind="reference".'
    if not target.is_file():
        return f"Error: not a file: {path}"
    text = await asyncio.to_thread(_line_text, target, line)
    matches = [m.start() for m in re.finditer(rf"(?<![\w]){re.escape(name)}(?![\w])", text)]
    if not matches:
        return f"Error: '{name}' doesn't appear on line {line} of {path}: {text.strip()[:160]!r}"
    # LSP positions count UTF-16 units, not characters (an emoji before the name
    # would otherwise point one token to the left).
    column = utf16_column(text, matches[min(max(occurrence, 1), len(matches)) - 1])

    navigator = navigator_for(workspace)
    if navigator is None:
        return 'Error: no Python language server installed (pip install basedpyright); use code_search.'
    query = navigator.definition if action == "definition" else navigator.references
    locations = await query(target, line, column)
    if locations is None:
        return "Error: the Python language server did not answer; use code_search kind=\"reference\"."
    if not locations:
        return f"No {action} found for '{name}' at {path}:{line} (unresolved import or dynamic code)."

    root = workspace.resolve()

    def show(loc) -> str:
        try:
            where = loc.path.resolve().relative_to(root).as_posix()
        except ValueError:
            where = str(loc.path)  # a library: still worth knowing where it lives
        return f"{where}:{loc.line}  {' '.join(_line_text(loc.path, loc.line).split())[:160]}"

    if action == "definition":
        return "\n".join(["Defined at:"] + [show(loc) for loc in locations])
    limit = min(max(limit if isinstance(limit, int) else DEFAULT_LIMIT, 1), MAX_LIMIT)

    def order(loc):
        try:
            rel = loc.path.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = "~" + str(loc.path)
        return _is_test_path(rel), rel.count("/"), rel, loc.line

    ordered = sorted(locations, key=order)
    lines = await asyncio.to_thread(lambda: [show(loc) for loc in ordered[:limit]])
    header = f"{len(locations)} references to '{name}' (exact, via the Python language server)"
    if len(locations) > limit:
        header += f"; {limit} shown, raise limit for more"
    return "\n".join([header, *lines])

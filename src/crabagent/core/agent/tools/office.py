from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from crabagent.core.agent.tools.registry import registry
from crabagent.core.event import AgentEvent, EventType
from crabagent.core.office.manager import get_office_manager

logger = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────

_SUPPORTED_EXTENSIONS = {".docx", ".xlsx", ".pptx"}


def _resolve_path(file_path: str, context: Any = None) -> str:
    """Resolve a relative path to an absolute path under the workspace."""
    if os.path.isabs(file_path):
        return file_path
    if context and hasattr(context, "workspace") and context.workspace:
        return str(Path(context.workspace).resolve() / file_path)
    return file_path


def _resolve_path_or_current(file_path: str, context: Any = None) -> tuple[str | None, str | None]:
    """Resolve the path; if file_path is empty, try to get the current document from context.

    Returns:
        (resolved_path, None) on success
        (None, error_message) on failure
    """
    if file_path:
        return _resolve_path(file_path, context), None
    # file_path empty → read current_doc from context metadata
    if context and context.metadata.get("current_doc"):
        doc = context.metadata["current_doc"]
        return _resolve_path(doc, context), None
    return None, "⚠️ Please specify a document path, or open a document in the file tree on the left first"


async def _ensure_available() -> str | None:
    """Ensure OfficeCLI is available, auto-detecting and auto-installing if needed. Returns an error message or None."""
    mgr = get_office_manager()
    if not mgr.available:
        # Try detection first (it may be installed at a non-standard path)
        if await mgr.detect():
            return None
        # Download and install automatically
        logger.info("OfficeCLI not found — attempting auto-install…")
        if await mgr.install():
            logger.info("OfficeCLI auto-install succeeded")
            return None
        # Installation failed; show installation guidance
        from crabagent.core.office.manager import _INSTALL_HINT
        return _INSTALL_HINT
    return None


async def _collect_formulas(mgr: Any, file_path: str, sheet: str = "") -> str:
    """Collect formula cells in an xlsx file and return a readable formula summary.

    Uses the query cell selector to find cells containing formulas, then fetches details one by one.
    """
    # Use query to find all cells with a formula
    result = await mgr.query(file_path, "cell[formula]")
    if not result.success or not result.data:
        return ""

    cells = result.data if isinstance(result.data, list) else result.data.get("results", [])
    if not cells:
        # query may return a dict
        if isinstance(result.data, dict):
            cells = result.data.get("results", [])

    if not cells:
        return ""

    lines: list[str] = []
    for cell_node in cells[:50]:  # Cap at 50 to avoid overly long output
        if isinstance(cell_node, dict):
            path = cell_node.get("path", "")
            fmt = cell_node.get("format", {})
            if isinstance(fmt, dict):
                formula = fmt.get("formula", "")
                cached = fmt.get("cachedValue", "")
                computed = fmt.get("computedValue", "")
                display = computed or cached or "?"
                lines.append(f"{path}: ={formula}  →  {display}")

    return "\n".join(lines) if lines else ""


def _check_extension(file_path: str) -> str | None:
    """Check whether the file extension is supported."""
    ext = Path(file_path).suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(_SUPPORTED_EXTENSIONS))
        return (
            f"Unsupported file format '{ext}'. "
            f"Supported formats: {supported}"
        )
    return None


def _validate_batch_commands(commands: list[dict[str, Any]]) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Validate and normalize batch commands before passing them to OfficeCLI."""
    if not isinstance(commands, list) or not commands:
        return None, "commands must be a non-empty array"

    allowed_commands = {"set", "add", "remove", "move", "swap"}
    normalized: list[dict[str, Any]] = []

    for idx, item in enumerate(commands, start=1):
        if not isinstance(item, dict):
            return None, f"command #{idx} must be an object"

        command = str(item.get("command", "")).strip().lower()
        if command not in allowed_commands:
            return None, f"command #{idx} is not supported: {command or '<empty>'}"

        entry: dict[str, Any] = {"command": command}
        path = item.get("path")
        parent = item.get("parent")

        # Compatible aliases: the model may reuse office_edit's element_path / element_type
        if path is None and isinstance(item.get("element_path"), str):
            path = item["element_path"]
        if parent is None and isinstance(item.get("element_path"), str):
            parent = item["element_path"]
        element_type = item.get("type")
        if element_type is None and isinstance(item.get("element_type"), str):
            element_type = item["element_type"]

        if command in {"set", "remove", "move", "swap"}:
            if not isinstance(path, str) or not path.strip():
                return None, f"command #{idx} is missing path"
            entry["path"] = path.strip()

        if command == "add":
            if not isinstance(parent, str) or not parent.strip():
                return None, f"add command #{idx} is missing parent (parent container path, e.g. /body, /slide[1])"
            if not isinstance(element_type, str) or not element_type.strip():
                return None, f"add command #{idx} is missing type (element type, e.g. paragraph, shape)"
            entry["parent"] = parent.strip()
            entry["type"] = element_type.strip()

        props = item.get("props")
        if props is not None:
            if not isinstance(props, dict):
                return None, f"props of command #{idx} must be an object"
            entry["props"] = props

        if command == "move":
            to_parent = item.get("to")
            after = item.get("after")
            before = item.get("before")
            has_target = isinstance(to_parent, str) and to_parent.strip()
            if not has_target and not (
                isinstance(after, str) and after.strip()
            ) and not (isinstance(before, str) and before.strip()):
                return None, (
                    f"move command #{idx} is missing a target: to (target parent path) "
                    "or after/before (reference element paths)"
                )
            if has_target:
                entry["to"] = to_parent.strip()
            for key in ("after", "before"):
                v = item.get(key)
                if isinstance(v, str) and v.strip():
                    entry[key] = v.strip()
            if "index" in item:
                try:
                    entry["index"] = int(item["index"])
                except (TypeError, ValueError):
                    return None, f"index of move command #{idx} must be an integer"

        if command == "swap":
            other = item.get("with")
            if not isinstance(other, str) or not other.strip():
                return None, f"swap command #{idx} is missing with"
            entry["with"] = other.strip()

        if command == "add":
            for key in ("index", "after", "before"):
                if key in item:
                    entry[key] = item[key]

        normalized.append(entry)

    return normalized, None


async def _emit(context: Any, event_type: EventType, data: dict[str, Any]) -> None:
    """Emit an SSE event (event_bus only exists in serve mode)."""
    if context and hasattr(context, "event_bus") and context.event_bus:
        try:
            await context.event_bus.emit(AgentEvent(type=event_type, data=data))
        except Exception:
            logger.debug("Failed to emit office event", exc_info=True)


async def _render_and_emit_preview(
    file_path: str, resolved: str, context: Any
) -> None:
    """Render a preview and emit it after a successful operation."""
    mgr = get_office_manager()
    preview = await mgr.view_html(resolved)
    if preview.success:
        await _emit(context, EventType.DOC_OP_PREVIEW, {
            "file": file_path,
            "html": preview.data,
        })
    else:
        logger.debug("Preview render skipped (not critical): %s", preview.error)


# ── tool: office_read ──────────────────────────────────────────────────


@registry.register(
    name="office_read",
    description="Read Office document content. Supports .docx (Word), .xlsx (Excel), .pptx (PowerPoint). "
    "Returns plain text or structured JSON for AI analysis of document data. "
    "For spreadsheets (including .ods/.csv, cell colours and notes) use spreadsheet_read instead.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Document path (absolute, or relative to the current workspace). "
                "Leave empty to automatically use the currently open document.",
            },
            "mode": {
                "type": "string",
                "enum": ["text", "outline", "stats"],
                "description": "Read mode: text-full text, outline-outline structure, stats-statistics",
                "default": "text",
            },
            "sheet": {
                "type": "string",
                "description": "(Excel only) worksheet name, e.g. 'Sheet1'. Leave empty to read all worksheets.",
            },
            "cols": {
                "type": "string",
                "description": "(Excel text mode only) column range, e.g. 'A:D', 'B:F'. "
                "Leave empty to read all columns.",
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum number of lines to return",
                "default": 200,
            },
            "offset": {
                "type": "integer",
                "description": "Starting line/paragraph number (1-based). "
                "Used to skip the beginning of the document and read the middle or later part directly.",
                "default": 0,
            },
        },
        "required": [],
    },
)
async def office_read(
    file_path: str = "",
    mode: str = "text",
    sheet: str = "",
    cols: str = "",
    max_lines: int = 200,
    offset: int = 0,
    context: Any = None,
) -> str:
    """Read Office document content."""
    err = await _ensure_available()
    if err:
        return err

    resolved, err_msg = _resolve_path_or_current(file_path, context)
    if err_msg:
        return err_msg

    err_ext = _check_extension(resolved)
    if err_ext:
        return err_ext

    if not os.path.isfile(resolved):
        return f"File not found: {resolved}"

    mgr = get_office_manager()

    if mode == "outline":
        result = await mgr.view_outline(resolved)
    elif mode == "stats":
        result = await mgr.view_stats(resolved)
    else:
        result = await mgr.view_text(
            resolved, max_lines=max_lines, sheet=sheet, cols=cols, start=offset
        )

    if not result.success:
        return f"Read failed: {result.error}"

    content = result.data or ""

    # xlsx: the CLI does not support --sheet filtering (it reports an unknown option);
    # its output already contains "=== Sheet: X ===" sections, so split by sheet here
    # at the tool layer.
    if (
        mode == "text"
        and resolved.endswith(".xlsx")
        and sheet
        and "=== Sheet:" in str(content)
    ):
        sections: dict[str, str] = {}
        current = ""
        buf: list[str] = []
        for line in str(content).splitlines():
            stripped = line.strip()
            if stripped.startswith("=== Sheet:") and stripped.endswith("==="):
                if current:
                    sections[current] = "\n".join(buf)
                current = stripped[len("=== Sheet:"):-len("===")].strip()
                buf = []
            else:
                buf.append(line)
        if current:
            sections[current] = "\n".join(buf)

        if sheet in sections:
            content = f"=== Sheet: {sheet} ===\n{sections[sheet]}"
        else:
            available = ", ".join(sections) if sections else "(unknown)"
            content = (
                f"{content}\n\n"
                f"⚠️ Worksheet '{sheet}' not found; returning all content. "
                f"Available worksheets: {available}"
            )

    # For xlsx files, append formula information in text mode
    if mode == "text" and resolved.endswith(".xlsx"):
        try:
            formula_info = await _collect_formulas(mgr, resolved, sheet)
            if formula_info:
                content += "\n\n--- Formula cells ---\n" + formula_info
        except Exception:
            logger.debug("Formula collection failed", exc_info=True)

    file_label = Path(resolved).name
    extra_parts: list[str] = []
    if sheet:
        extra_parts.append(f"sheet={sheet}")
    if cols and resolved.endswith(".xlsx") and mode == "text":
        extra_parts.append(f"cols={cols}")
    extra = f", {', '.join(extra_parts)}" if extra_parts else ""
    summary = f"📄 Read {file_label} ({mode} mode{extra}, {len(str(content))} characters)\n\n"
    return summary + str(content)


@registry.register(
    name="office_help",
    description="Look up OfficeCLI help reference for a given document format, returning commonly used "
    "element paths, properties and capability notes. "
    "Useful for confirming which fields docx/xlsx/pptx support before editing.",
    parameters={
        "type": "object",
        "properties": {
            "file_format": {
                "type": "string",
                "enum": ["docx", "xlsx", "pptx"],
                "description": "Document format to query.",
            },
        },
        "required": ["file_format"],
    },
)
async def office_help(file_format: str = "", context: Any = None) -> str:
    """Query OfficeCLI help for a supported format."""
    del context  # reserved for future event integration

    err = await _ensure_available()
    if err:
        return err

    fmt = (file_format or "").strip().lower().lstrip(".")
    if fmt not in {"docx", "xlsx", "pptx"}:
        return "Please specify file_format as docx, xlsx or pptx"

    mgr = get_office_manager()
    result = await mgr.help_for(fmt)
    if not result.success:
        return f"Failed to get help: {result.error}"

    content = str(result.data or "").strip()
    if not content:
        return f"{fmt} returned no help content"

    max_chars = 20_000
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n... (help content truncated; focus on a specific format for further questions)"

    return f"📚 OfficeCLI {fmt} help\n\n{content}"


# ── tool: office_create ────────────────────────────────────────────────


@registry.register(
    name="office_batch_edit",
    description="Batch-edit multiple elements in an Office document, executing multiple commands within "
    "a single open/save cycle. "
    "Suitable for completing multi-step set/add/remove/move/swap operations at once, avoiding repeated saves.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Document path. Leave empty to automatically use the currently open document.",
            },
            "commands": {
                "type": "array",
                "description": "Batch command list; each command is an object. Fields differ from the "
                "single-command office_edit, ⚠️ note:\n"
                "- set/remove/move/swap use \"path\" (element path)\n"
                "- add uses \"parent\" (parent container path) + \"type\" (element type), "
                "not element_path/element_type\n"
                "- move requires \"to\" (target parent path), optional \"index\"; "
                "swap requires \"with\" (another path)\n"
                "- optional \"props\" object passes style/text; add can use \"after\"/\"before\" "
                "to specify reference elements for insertion\n"
                "Example:\n"
                '[{"command":"add","parent":"/body","type":"paragraph","props":{"text":"Title","size":22,"bold":true}},\n'
                ' {"command":"set","path":"/body/p[1]","props":{"color":"#FF0000"}},\n'
                ' {"command":"remove","path":"/body/p[3]"}]',
                "items": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "enum": ["set", "add", "remove", "move", "swap"],
                        },
                        "path": {"type": "string", "description": "Element path for set/remove/move/swap"},
                        "parent": {"type": "string", "description": "Parent container path for add, e.g. /body, /slide[1], /Sheet1"},
                        "type": {"type": "string", "description": "Element type for add, e.g. paragraph, shape, slide, row"},
                        "to": {"type": "string", "description": "Target parent path for move"},
                        "with": {"type": "string", "description": "The other element path for swap"},
                        "props": {"type": "object", "description": "Property key-value pairs, e.g. text, size, bold, color"},
                    },
                    "required": ["command"],
                },
            },
        },
        "required": ["commands"],
    },
)
async def office_batch_edit(
    file_path: str = "",
    commands: list[dict[str, Any]] | None = None,
    context: Any = None,
) -> str:
    """Batch-edit an Office document."""
    err = await _ensure_available()
    if err:
        return err

    resolved, err_msg = _resolve_path_or_current(file_path, context)
    if err_msg:
        return err_msg

    err_ext = _check_extension(resolved)
    if err_ext:
        return err_ext

    if not os.path.isfile(resolved):
        return f"File not found: {resolved}"

    batch_commands, batch_err = _validate_batch_commands(commands or [])
    if batch_err:
        return batch_err

    file_label = Path(resolved).name
    await _emit(context, EventType.DOC_OP_DELTA, {
        "file": file_label,
        "message": f"📦 batch {len(batch_commands or [])} steps",
        "commands": batch_commands,
    })

    mgr = get_office_manager()
    result = await mgr.batch(resolved, batch_commands or [])
    if not result.success:
        return f"Batch operation failed: {result.error}"

    await _render_and_emit_preview(file_label, resolved, context)
    return f"✅ Executed {len(batch_commands or [])} batch operations"


@registry.register(
    name="office_create",
    description="Create a new Office document. Supports .docx (Word), .xlsx (Excel), .pptx (PowerPoint). "
    "Returns the file path after creation; use office_edit to add content.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path of the document to create (absolute, or relative to the workspace). "
                "Leave empty to automatically use the currently open document.",
            },
        },
        "required": [],
    },
)
async def office_create(file_path: str = "", context: Any = None) -> str:
    """Create a new Office document."""
    err = await _ensure_available()
    if err:
        return err

    resolved, err_msg = _resolve_path_or_current(file_path, context)
    if err_msg:
        return err_msg

    err_ext = _check_extension(resolved)
    if err_ext:
        return err_ext

    # If the file already exists, confirm first
    if os.path.isfile(resolved):
        return (
            f"File already exists: {file_path}\n"
            f"Use office_edit to modify it, or delete it first."
        )

    # Ensure the parent directory exists
    Path(resolved).parent.mkdir(parents=True, exist_ok=True)

    await _emit(context, EventType.DOC_OP_START, {
        "file": file_path,
        "operation": "create",
    })

    mgr = get_office_manager()
    result = await mgr.create(resolved)

    if result.success:
        await _emit(context, EventType.DOC_OP_DONE, {
            "file": file_path,
            "status": "ok",
            "message": f"✅ Document created: {file_path}",
        })
        # Try rendering a preview
        preview = await mgr.view_html(resolved)
        if preview.success:
            await _emit(context, EventType.DOC_OP_PREVIEW, {
                "file": file_path,
                "html": preview.data,
            })
        return f"✅ Document created: {file_path}\nFile size: {os.path.getsize(resolved)} bytes"
    else:
        await _emit(context, EventType.DOC_OP_DONE, {
            "file": file_path,
            "status": "error",
            "message": f"Creation failed: {result.error}",
        })
        return f"Creation failed: {result.error}"


# ── tool: office_edit ──────────────────────────────────────────────────


_EDIT_DESCRIPTIONS = {
    "set": "✏️ set",
    "add": "➕ add",
    "remove": "🗑️ remove",
    "move": "↗️ move",
}


def _describe_op(
    command: str, element_path: str, props: dict[str, Any] | None
) -> str:
    icon = _EDIT_DESCRIPTIONS.get(command, "📄")
    text = ""
    if props:
        text = props.get("text", props.get("title", ""))
        if isinstance(text, str) and len(text) > 60:
            text = text[:60] + "…"
    desc = f"{icon} {command} {element_path}"
    if text:
        desc += f' "{text}"'
    return desc


@registry.register(
    name="office_edit",
    description="Edit elements in an Office document. Supports modifying text/style (set), adding "
    "elements (add), removing elements (remove) and moving elements (move). "
    "Works with Word/Excel/PowerPoint.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Document path. Leave empty to automatically use the currently open document.",
            },
            "command": {
                "type": "string",
                "enum": ["set", "add", "remove", "move", "swap"],
                "description": "Operation type:\n"
                "- set: modify properties of an existing element (text, font, color, size, formula, merge, etc.)\n"
                "- add: add a new element (slide, shape, table, row, column, cell, etc.).\n"
                "  ⚠️ For add, element_path should be the **parent container path**, not the exact insertion point.\n"
                "  For example: add a row in Excel → element_path=/Sheet1 (not /Sheet1/row[5])\n"
                "               add a slide in PPT → element_path=/ (not /slide[3])\n"
                "               add a paragraph in Word → element_path=/body (not /body/p[2])\n"
                "  To insert at a specific position, use props.after or props.before to specify a reference element.\n"
                "- remove: remove an element\n"
                "- move: move an element. The target goes in props: props.to=target parent path, "
                "or props.after=/props.before=reference element path, optional props.index=insertion position\n"
                "- swap: swap two elements; the second element path goes in props.with",
            },
            "element_path": {
                "type": "string",
                "description": "Element path. Common paths:\n"
                "- / → document root\n"
                "- /slide[1] → slide 1\n"
                "- /slide[1]/shape[1] → shape 1 on slide 1\n"
                "- /Sheet1/A1 → cell A1 in Excel (note: sheet names are case-sensitive; use letter column refs)\n"
                "- /Sheet1/A1:C3 → Excel range (for merging / batch formatting)\n"
                "- /Sheet1/row[3] → row 3 in Excel (for setting row height / deleting rows)\n"
                "- /Sheet1/col[A] → column A in Excel (for setting column width / deleting columns)\n"
                "- /theme → PPT theme (color/font scheme)\n"
                "- /slidemaster[1] → PPT slide master\n"
                "- /body/p[1] → paragraph 1 in Word\n"
                "- /body/tbl[1]/tr[2]/tc[1] → row 2, column 1 cell of the first Word table\n"
                "- /slide[1]/table[1]/tr[2]/tc[1] → row 2, column 1 cell of the first PPT table\n\n"
                "⚠️ add command: element_path is the **parent container path** (e.g. /Sheet1, /body, /).\n"
                "   To insert after a specific element, set after=/Sheet1/row[5] in props.\n\n"
                "⚠️ Excel note: a newly created blank worksheet has no preset cells.\n"
                "   First use the add command to add cells (e.g. add → parent=/Sheet1, type=cell),\n"
                "   then use set to modify their content.\n"
                "   add automatically creates rows and cells; paths look like /Sheet1/A1, /Sheet1/B2, etc.",
            },
            "props": {
                "type": "object",
                "description": "Property key-value pairs to set. Common properties:\n"
                "- text: text content\n"
                "- title: slide title\n"
                "- font: font name\n"
                "- size: font size (pt)\n"
                "- color: color (hex #FF0000 or named color)\n"
                "- background: background color\n"
                "- width, height: dimensions\n"
                "- x, y: position\n\n"
                "Excel-specific properties:\n"
                "- formula: cell formula (without the = prefix), e.g. SUM(A1:A10)\n"
                "- merge: merge cells; pass a range like A1:C3, or true/false (for range paths)\n"
                "- numberformat: number format, e.g. #,##0.00 or yyyy-mm-dd\n"
                "- fill: cell background color\n"
                "- border.all: border style, e.g. 'single;1pt;FF0000'\n"
                "- bold/italic/underline/strike: font styles\n"
                "- alignment.horizontal: alignment (left/center/right)\n"
                "- shift: shift direction when inserting (right/down)\n\n"
                "Word table-specific properties:\n"
                "- colspan: number of columns to merge horizontally\n"
                "- vmerge: vertical merge (restart marks the start, continue marks the following cells)\n"
                "- valign: vertical alignment (top/center/bottom)\n\n"
                "PPT-specific properties:\n"
                "- layout: slide layout name (e.g. 'Title Slide', 'Title and Content', 'Blank')\n"
                "- merge.right: number of table columns to merge horizontally\n"
                "- transition: slide transition effect (fade/push/wipe/morph)\n\n"
                "PPT theme properties (path /theme):\n"
                "- accent1~accent6: theme accent colors\n"
                "- headingFont: heading font / bodyFont: body font\n\n"
                "Table creation properties (add table):\n"
                "- data: table data. Two formats supported:\n"
                "  - JSON 2D array (recommended): [['A','B'],['1','2']]\n"
                "  - CSV string (legacy): 'A,B;1,2'\n"
                "- style: table style (medium1~4, light1~3, dark1~2)\n"
                "- headerFill/bodyFill: header/body background color\n"
                "- border.all/border.horizontal/border.vertical: borders",
            },
            "element_type": {
                "type": "string",
                "description": "(add only) Element type to add:\n"
                "- slide: slide (PPT) — supports layout/title/text properties\n"
                "- shape: shape/text box\n"
                "- table: table — supports data/style/headerFill properties\n"
                "- chart: chart\n"
                "- image: image\n"
                "- sheet: worksheet (Excel)\n"
                "- row: row (Excel: add to /SheetName — supports --after/--before to insert at a specific position)\n"
                "- column: column (Excel: add to /SheetName — --prop name=C sets the column letter)\n"
                "- cell: cell (Excel: add to /SheetName — --prop ref=B2 sets the position)\n"
                "- paragraph: paragraph (Word)\n"
                "- placeholder: PPT placeholder (--prop phType=title/body)",
            },
        },
        "required": ["command"],
    },
)
async def office_edit(
    file_path: str = "",
    command: str = "",
    element_path: str = "",
    props: dict[str, Any] | None = None,
    element_type: str = "",
    context: Any = None,
) -> str:
    """Edit elements in an Office document."""
    err = await _ensure_available()
    if err:
        return err

    resolved, err_msg = _resolve_path_or_current(file_path, context)
    if err_msg:
        return err_msg

    err_ext = _check_extension(resolved)
    if err_ext:
        return err_ext

    if not os.path.isfile(resolved):
        return f"File not found: {resolved}"

    mgr = get_office_manager()
    props = props or {}

    # Emit a progress event
    desc = _describe_op(command, element_path, props)
    await _emit(context, EventType.DOC_OP_DELTA, {
        "file": file_path,
        "message": desc,
        "element_path": element_path,
    })

    # Execute the operation
    if command == "set":
        result = await mgr.set_props(resolved, element_path, props)
    elif command == "add":
        if not element_type:
            return "add requires element_type (e.g. slide, shape, table)"

        # Smart path normalization: if the AI passed a specific child path
        # like /Sheet1/row[19] instead of the parent /Sheet1, extract the
        # parent and use --after to insert at the expected position.
        add_parent = element_path
        add_props = dict(props)
        # The element type comes from the element_type argument; strip any redundant
        # "type" that ended up in props so --prop type=... does not confuse the CLI
        # (consistent with the batch path behaviour).
        add_props.pop("type", None)
        if "[" in element_path and element_path.count("/") >= 2:
            # e.g. /Sheet1/row[19] → parent=/Sheet1, after=/Sheet1/row[19]
            parts = element_path.rsplit("/", 1)
            parent_candidate = parts[0]
            child_part = parts[1]
            if "[" in child_part:
                # This looks like a specific element path, not a parent
                add_parent = parent_candidate
                if "after" not in add_props and "before" not in add_props and "index" not in add_props:
                    add_props["after"] = element_path
                    logger.info("add: normalized parent %s → %s, after=%s",
                                element_path, add_parent, element_path)

        result = await mgr.add_element(resolved, add_parent, element_type, add_props)
    elif command == "remove":
        result = await mgr.remove_element(resolved, element_path)
    elif command == "move":
        to_parent = str(props.get("to", "") or "")
        after = str(props.get("after", "") or "")
        before = str(props.get("before", "") or "")
        index = props.get("index", -1)
        if not to_parent and not after and not before:
            return (
                "move requires a target: props.to (target parent path, e.g. /body), "
                "or props.after / props.before (reference element paths)"
            )
        result = await mgr.move_element(
            resolved, element_path, to_parent, int(index), after=after, before=before
        )
    elif command == "swap":
        path2 = props.get("with", "")
        if not path2:
            return "swap requires props.with (the path of the second element)"
        result = await mgr.exec("swap", resolved, element_path, path2)
    else:
        return f"Unsupported command: {command}"

    if result.success:
        # Render preview
        await _render_and_emit_preview(file_path, resolved, context)
        actual_path = ""
        if hasattr(result, "data") and result.data:
            actual_path = str(result.data).strip()
        detail = _describe_op(command, element_path, props)
        if actual_path and "Added" in actual_path:
            detail += f" → {actual_path}"
        return f"✅ {detail}"
    else:
        return f"Operation failed: {result.error}"


# ── tool: office_query ─────────────────────────────────────────────────


@registry.register(
    name="office_query",
    description="Query elements in an Office document. Supports fetching structured JSON by path, "
    "or finding elements by condition using a selector.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Document path. Leave empty to automatically use the currently open document.",
            },
            "path_or_selector": {
                "type": "string",
                "description": "Element path (e.g. /slide[1]/shape[1]) or selector expression. "
                "Selector examples:\n"
                '- "shape:contains(TODO)" → find shapes containing TODO\n'
                '- "text:contains(report)" → find text containing "report"\n'
                '- "table:*" → find all tables',
            },
            "mode": {
                "type": "string",
                "enum": ["path", "selector"],
                "description": "Query mode: path-fetch by path, selector-search by selector",
                "default": "path",
            },
            "depth": {
                "type": "integer",
                "description": "(path mode) recursion depth",
                "default": 1,
            },
        },
        "required": ["path_or_selector"],
    },
)
async def office_query(
    file_path: str = "",
    path_or_selector: str = "",
    mode: str = "path",
    depth: int = 1,
    context: Any = None,
) -> str:
    """Query document elements."""
    err = await _ensure_available()
    if err:
        return err

    resolved, err_msg = _resolve_path_or_current(file_path, context)
    if err_msg:
        return err_msg

    err_ext = _check_extension(resolved)
    if err_ext:
        return err_ext

    if not os.path.isfile(resolved):
        return f"File not found: {resolved}"

    mgr = get_office_manager()

    if mode == "selector":
        result = await mgr.query(resolved, path_or_selector)
    else:
        result = await mgr.get_element(resolved, path_or_selector, depth=depth)

    if not result.success:
        return f"Query failed: {result.error}"

    data = result.data
    if isinstance(data, (dict, list)):
        import json as _json

        output = _json.dumps(data, ensure_ascii=False, indent=2)
        # Truncate overly long output to avoid returning hundreds of thousands of characters
        max_chars = 50_000
        if len(output) > max_chars:
            output = (
                output[:max_chars]
                + "\n\n... (output truncated, "
                + str(len(output))
                + " characters total. Use a more precise path or increase depth to narrow the scope)"
            )
        return output
    return str(data)


# ── tool: office_render ────────────────────────────────────────────────


@registry.register(
    name="office_render",
    description="Render an Office document as an HTML preview. The returned HTML can be displayed "
    "directly in a browser, "
    "letting users see the visual presentation of the document.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Document path. Leave empty to automatically use the currently open document.",
            },
        },
        "required": [],
    },
)
async def office_render(file_path: str = "", context: Any = None) -> str:
    """Render the document as an HTML preview."""
    err = await _ensure_available()
    if err:
        return err

    resolved, err_msg = _resolve_path_or_current(file_path, context)
    if err_msg:
        return err_msg

    err_ext = _check_extension(resolved)
    if err_ext:
        return err_ext

    if not os.path.isfile(resolved):
        return f"File not found: {resolved}"

    mgr = get_office_manager()
    result = await mgr.view_html(resolved)

    if not result.success:
        return f"Rendering failed: {result.error}"

    html = result.data or ""

    await _emit(context, EventType.DOC_OP_PREVIEW, {
        "file": file_path,
        "html": html,
    })

    return html

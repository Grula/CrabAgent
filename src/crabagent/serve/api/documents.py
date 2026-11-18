from __future__ import annotations

import asyncio
import datetime
import json
import logging
import mimetypes
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from crabagent.core.config import settings
from crabagent.core.database import User
from crabagent.core.office.manager import get_office_manager
from crabagent.serve.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_PREVIEWABLE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}

# ── helpers ───────────────────────────────────────────────────────


async def _ensure_officecli() -> bool:
    """Ensure OfficeCLI is usable, detecting and installing it automatically.

    When installation takes a while, the install task is started in the
    background and ``False`` is returned; the frontend can poll progress via
    ``GET /api/officecli/status``.

    Returns:
        True -- OfficeCLI is ready and can be used right away
        False -- an install task was started and must finish first

    Raises:
        HTTPException(503) -- the install already failed; do not retry
    """
    mgr = get_office_manager()
    if mgr.available:
        return True
    if await mgr.detect():
        return True

    status = mgr.get_install_status()

    # Install already failed -> fail fast, no retry
    if status["status"] == "failed":
        raise HTTPException(status_code=503, detail=status.get("message", "OfficeCLI installation failed"))

    # Install in progress -> return False, the frontend keeps polling
    if status["status"] == "installing":
        return False

    # Not installed -> kick off the install in the background
    mgr._install_task = asyncio.create_task(mgr.install())
    return False


def _get_docs_dir(user_id: int, workspace: str = "") -> Path:
    """Get the user's document directory, creating it if needed."""
    if hasattr(settings, "docs_dir") and settings.docs_dir:
        base = Path(settings.docs_dir) / str(user_id)
    else:
        base = Path.home() / ".crabagent" / "docs" / str(user_id)
    if workspace:
        base = base / workspace
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_path(base_dir: Path, file_name: str) -> Path:
    """Resolve a file name safely within base_dir, preventing path traversal."""
    full = base_dir.resolve() / file_name
    full = full.resolve()
    if not str(full).startswith(str(base_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid file path")
    return full


# ── routes ─────────────────────────────────────────────────────────


@router.get("/download")
async def download_document(
    path: str,
    workspace: str = "",
    user: User = Depends(get_current_user),
):
    """Download a document. Accepts ?path=relative/path or ?path=name (workspace root)."""
    docs_dir = _get_docs_dir(user.id, workspace)
    file_path = _safe_path(docs_dir, path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(str(file_path), filename=file_path.name, media_type=media_type)


@router.get("/preview")
async def preview_document(
    path: str,
    workspace: str = "",
    user: User = Depends(get_current_user),
):
    """Return an HTML preview of the document (requires OfficeCLI)."""
    if not await _ensure_officecli():
        return JSONResponse(status_code=503, content={
            "detail": "OfficeCLI is being installed, please retry",
            "installing": True,
        })
    mgr = get_office_manager()
    docs_dir = _get_docs_dir(user.id, workspace)
    file_path = _safe_path(docs_dir, path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    if ext not in _PREVIEWABLE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type '{ext}' is not previewable")

    result = await mgr.view_html(str(file_path))
    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)

    return {"html": _fix_html_newlines(result.data), "file": path, "name": file_path.name}


# ── Quick Edit ──────────────────────────────────────────────────────


class StyleChange(BaseModel):
    """A single style/layout property change."""
    element: str
    """Element path, e.g. /Sheet1/col[A] or /Sheet1/row[3]"""
    props: dict[str, str | int | float | bool]
    """Properties to set, e.g. {"width": 20} or {"height": 30, "hidden": True}"""


class QuickEditStyleRequest(BaseModel):
    """Request: batch-update style/layout properties of document elements (row height, column width, ...)."""
    path: str
    workspace: str = ""
    changes: list[StyleChange]


class QuickEditStyleResponse(BaseModel):
    status: str
    preview_html: str | None = None
    results: list[dict] = []
    message: str = ""


class QuickEditTextRequest(BaseModel):
    """Request: change document text by matching on the existing text."""

    path: str
    old_text: str
    new_text: str
    workspace: str = ""


class QuickEditTextResponse(BaseModel):
    status: str
    preview_html: str | None = None
    message: str = ""


def _backup_doc(file_path: Path) -> str | None:
    """Create a backup copy of the document and return its path; None on failure."""
    try:
        backup_dir = Path.home() / ".crabagent" / "docs-backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{file_path.stem}_{ts}{file_path.suffix}"
        shutil.copy2(str(file_path), str(backup_path))
        return str(backup_path)
    except Exception as e:
        logger.warning("Backup failed for %s: %s", file_path, e)
        return None


def _fix_html_newlines(html: str | None) -> str | None:
    """OfficeCLI's HTML renderer does not turn \\n into <br>, so we inject a
    CSS ``white-space: pre-wrap`` rule into the head to make newlines visible."""
    if html:
        # Inject a single CSS rule into <head> (or before <html>) so all text keeps its line breaks
        css = "<style>.page, .page-body, .page * { white-space: pre-wrap !important; }</style>\n"
        if "<head>" in html:
            html = html.replace("<head>", "<head>" + css)
        elif "<html" in html:
            html = html.replace("<html", css + "<html")
        else:
            html = css + html
    return html


async def _render_preview_html(file_path: Path) -> str | None:
    """Render document preview HTML after a quick-edit mutation."""
    mgr = get_office_manager()
    preview_result = await mgr.view_html(str(file_path))
    return _fix_html_newlines(preview_result.data) if preview_result.success else None


@router.post("/quick-edit/style", response_model=QuickEditStyleResponse)
async def quick_edit_style(
    req: QuickEditStyleRequest,
    user: User = Depends(get_current_user),
):
    """Batch-update style/layout properties of document elements
    (row height, column width, hidden rows/columns, ...).

    Called by the frontend to persist a dragged column border, for example.
    Each change maps to one ``officecli set`` operation.
    """
    if not await _ensure_officecli():
        return JSONResponse(status_code=503, content={"installing": True, "detail": "OfficeCLI is being installed"})
    mgr = get_office_manager()

    docs_dir = _get_docs_dir(user.id, req.workspace)
    file_path = _safe_path(docs_dir, req.path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    if ext not in _PREVIEWABLE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}'")

    if not req.changes:
        raise HTTPException(status_code=400, detail="No changes provided")

    logger.info(
        "[QE-style] %d change(s) on %s",
        len(req.changes), req.path,
    )

    # Back up before mutating
    _backup_doc(file_path)

    results: list[dict] = []
    all_ok = True

    for change in req.changes:
        try:
            result = await mgr.set_props(
                str(file_path), change.element, dict(change.props)
            )
            results.append({
                "element": change.element,
                "success": result.success,
                "error": result.error if not result.success else "",
            })
            if not result.success:
                all_ok = False
                logger.warning("[QE-style] Failed: %s → %s", change.element, result.error)
            else:
                logger.info("[QE-style] OK: %s → %s", change.element, change.props)
        except Exception as e:
            results.append({
                "element": change.element,
                "success": False,
                "error": str(e),
            })
            all_ok = False
            logger.exception("[QE-style] Error on %s", change.element)

    # Render a fresh preview (returned even on partial failure so the frontend sees the current state)
    preview_html = await _render_preview_html(file_path)

    return QuickEditStyleResponse(
        status="ok" if all_ok else "partial",
        preview_html=preview_html,
        results=results,
        message=f"{len(req.changes)} change(s), {sum(1 for r in results if r['success'])} succeeded",
    )


@router.post("/quick-edit/text", response_model=QuickEditTextResponse)
async def quick_edit_text(
    req: QuickEditTextRequest,
    user: User = Depends(get_current_user),
):
    """Quickly change document text by matching on the existing text.

    Called after a user double-clicks text in the preview and edits it.
    The backend uses OfficeCLI's global ``set --find / --replace``, so no
    element path has to be resolved up front.
    """
    if not await _ensure_officecli():
        return JSONResponse(status_code=503, content={"installing": True, "detail": "OfficeCLI is being installed"})
    mgr = get_office_manager()

    # Resolve the file path
    docs_dir = _get_docs_dir(user.id, req.workspace)
    file_path = _safe_path(docs_dir, req.path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    if ext not in _PREVIEWABLE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}'")

    try:
        old = req.old_text.strip()
        new = req.new_text.strip()
        if not old:
            raise HTTPException(status_code=400, detail="old_text is required")

        logger.info("[QE] old=%s new=%s split=%s", old[:50], new[:50], "\n" in new)

        # Back up before mutating
        backup_path = _backup_doc(file_path)
        if backup_path:
            logger.info("[QE] Backed up %s", file_path.name)

        # Decide whether the text must be split into paragraphs (new text contains \n)
        need_split = "\n" in new

        if not need_split:
            logger.info("[QE] Simple text replace")
            # Simple case: plain text replacement via batch set --find/--replace
            batch_cmds = json.dumps([{
                "command": "set",
                "path": "/",
                "props": {"find": old, "replace": new},
            }]).encode("utf-8")
            binary = mgr.binary_path
            if not binary:
                raise HTTPException(status_code=503, detail="OfficeCLI is not installed")
            result = await mgr._exec_with_stdin(
                [binary, "batch", str(file_path), "--json"],
                stdin_data=batch_cmds,
            )
            if not result.success:
                raise HTTPException(status_code=500, detail=f"Text replace failed: {result.error}")

        else:
            logger.info("[QE] Paragraph split mode: %d segments", len([s for s in new.split("\n") if s.strip()]))
            # Complex case: the user pressed Enter, so the original paragraph must be split
            # 1) Locate the original paragraph with officecli query "paragraph" --find <old_text>
            binary = mgr.binary_path
            if not binary:
                raise HTTPException(status_code=503, detail="OfficeCLI is not installed")
            query_result = await mgr._exec_with_stdin(
                [binary, "query", str(file_path), "paragraph", "--find", old, "--json"],
            )
            para_path = None
            if query_result.success and isinstance(query_result.data, dict):
                results = query_result.data.get("results", [])
                if results:
                    para_path = results[0].get("path", "")
            if not para_path:
                raise HTTPException(status_code=404, detail=f"Paragraph with text '{old}' not found")

            parent_path = "/body"
            parent_info = await mgr.get_element(str(file_path), parent_path, depth=1)
            para_index = None
            if parent_info.success and isinstance(parent_info.data, dict):
                children = parent_info.data.get("results", [{}])[0].get("children", [])
                for i, child in enumerate(children):
                    if child.get("path") == para_path:
                        para_index = i
                        break

            if para_index is None:
                raise HTTPException(status_code=500, detail="Could not determine paragraph position")

            segments = [s for s in new.split("\n") if s.strip()]
            if not segments:
                raise HTTPException(status_code=400, detail="No content after splitting")

            # Remove the original paragraph via batch
            remove_result = await mgr._exec_with_stdin(
                [binary, "batch", str(file_path), "--json"],
                stdin_data=json.dumps([{"command": "remove", "path": para_path}]).encode("utf-8"),
            )
            if not remove_result.success:
                raise HTTPException(status_code=500, detail=f"Remove failed: {remove_result.error}")

            # Insert the new paragraphs one by one
            for idx, seg in enumerate(segments):
                add_result = await mgr.exec(
                    "add", str(file_path), parent_path,
                    "--type", "paragraph",
                    "--prop", f"text={seg.strip()}",
                    "--index", str(para_index + idx),
                    "--json",
                )
                if not add_result.success:
                    logger.error("[QE] Add paragraph failed at index %d: %s", para_index + idx, add_result.error)

        preview_html = await _render_preview_html(file_path)

        return QuickEditTextResponse(
            status="ok",
            preview_html=preview_html,
            message="done",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[QE] Unexpected error")
        raise HTTPException(status_code=500, detail=str(e))


# ── Quick Edit: structured table operations ───────────────────────────


class TableOpRequest(BaseModel):
    """Request: structured table operation (insert/delete rows or columns, merge/unmerge, set formula, ...)."""

    path: str
    workspace: str = ""
    operation: str
    """Operation type: insert_row | delete_row | insert_col | delete_col |
    merge_cells | unmerge_cells | set_formula | set_cell_style"""
    sheet: str = "Sheet1"
    """Excel worksheet name"""
    params: dict = {}
    """Operation parameters, which depend on the operation type:
    - insert_row: {after_row: int} or {before_row: int}
    - delete_row: {row: int}
    - insert_col: {col_letter: str} or {after_col: str}
    - delete_col: {col_letter: str}
    - merge_cells: {range: str}  e.g. "A1:C3"
    - unmerge_cells: {range: str}
    - set_formula: {cell: str, formula: str}
    - set_cell_style: {cell: str, props: dict}
    """


class TableOpResponse(BaseModel):
    status: str
    preview_html: str | None = None
    message: str = ""
    error: str = ""


def _build_batch_cmd(req: TableOpRequest) -> dict | None:
    """Translate a structured operation into an officecli batch command."""
    op = req.operation
    p = req.params
    sheet = req.sheet

    if op == "insert_row":
        cmd: dict = {"command": "add", "parent": f"/{sheet}", "type": "row", "props": {"cols": p.get("cols", 1)}}
        if "after_row" in p:
            cmd["after"] = f"/{sheet}/row[{p['after_row']}]"
        elif "before_row" in p:
            cmd["before"] = f"/{sheet}/row[{p['before_row']}]"
        return cmd

    if op == "delete_row":
        return {"command": "remove", "path": f"/{sheet}/row[{p['row']}]"}

    if op == "insert_col":
        cmd = {"command": "add", "parent": f"/{sheet}", "type": "column", "props": {}}
        if "col_letter" in p:
            cmd["props"]["name"] = p["col_letter"]
        if "after_col" in p:
            cmd["after"] = f"/{sheet}/col[{p['after_col']}]"
        return cmd

    if op == "delete_col":
        return {"command": "remove", "path": f"/{sheet}/col[{p['col_letter']}]"}

    if op == "merge_cells":
        cell_range = p.get("range", "")
        if not cell_range:
            return None
        return {"command": "set", "path": f"/{sheet}/{cell_range}", "props": {"merge": True}}

    if op == "unmerge_cells":
        cell_range = p.get("range", "")
        if not cell_range:
            return None
        return {"command": "set", "path": f"/{sheet}/{cell_range}", "props": {"merge": False}}

    if op == "set_formula":
        cell = p.get("cell", "")
        formula = p.get("formula", "")
        if not cell or not formula:
            return None
        return {"command": "set", "path": f"/{sheet}/{cell}", "props": {"formula": formula}}

    if op == "set_cell_style":
        cell = p.get("cell", "")
        props = p.get("props", {})
        if not cell or not props:
            return None
        return {"command": "set", "path": f"/{sheet}/{cell}", "props": props}

    return None


@router.post("/quick-edit/table-op", response_model=TableOpResponse)
async def quick_edit_table_op(
    req: TableOpRequest,
    user: User = Depends(get_current_user),
):
    """Run a structured table operation (insert/delete rows or columns, merge/unmerge, set formula, ...).

    Called by the frontend table action buttons (merge cells, insert row, ...).
    """
    if not await _ensure_officecli():
        return JSONResponse(status_code=503, content={"installing": True, "detail": "OfficeCLI is being installed"})
    mgr = get_office_manager()

    docs_dir = _get_docs_dir(user.id, req.workspace)
    file_path = _safe_path(docs_dir, req.path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    if ext not in _PREVIEWABLE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}'")

    batch_cmd = _build_batch_cmd(req)
    if batch_cmd is None:
        raise HTTPException(status_code=400, detail=f"Invalid operation or params: {req.operation}")

    logger.info("[QE-table] %s on %s/%s params=%s", req.operation, req.path, req.sheet, req.params)

    _backup_doc(file_path)

    try:
        # Run a single command through batch (one open/save cycle)
        result = await mgr.batch(str(file_path), [batch_cmd])

        if not result.success:
            return TableOpResponse(
                status="error",
                error=result.error or "Unknown error",
                message=f"Operation failed: {req.operation}",
            )

        preview_html = await _render_preview_html(file_path)

        return TableOpResponse(
            status="ok",
            preview_html=preview_html,
            message=f"✅ {req.operation} succeeded",
        )
    except Exception as e:
        logger.exception("[QE-table] Error")
        raise HTTPException(status_code=500, detail=str(e))


# ── Quick Edit: PPT theme ─────────────────────────────────────────────


class ThemeEditRequest(BaseModel):
    """Request: change the PPT theme colors/fonts."""

    path: str
    workspace: str = ""
    props: dict[str, str]
    """Theme properties:
    - accent1~accent6: theme accent colors (hex, e.g. 4472C4)
    - headingFont: heading font
    - bodyFont: body font
    - dk1/lt1/dk2/lt2: base colors
    - hyperlink: hyperlink color
    """


class StructureEditRequest(BaseModel):
    """Request: advanced structural editing (PPT / Word / Excel)."""

    path: str
    workspace: str = ""
    operations: list[dict]
    """Example operation list:
    - PPT slide: {"command":"add","parent":"/","type":"slide","props":{"layout":"Title Slide","title":"Q3 Review"}}
    - PPT placeholder: {"command":"add","parent":"/slide[1]","type":"placeholder","props":{"phType":"body","text":"content"}}
    - PPT image/chart: {"command":"add","parent":"/slide[1]","type":"image|chart","props":{...}}
    - Word paragraph: {"command":"add","parent":"/body","type":"paragraph","props":{"text":"new paragraph"}}
    - Word table cell merge: {"command":"set","path":"/body/tbl[1]/tr[2]/tc[1]","props":{"colspan":2}}
    - Excel style/range: {"command":"set","path":"/Sheet1/A1:C3","props":{"fill":"#FFF2CC","border.all":"single;1pt;000000"}}
    """


class StructureEditResponse(BaseModel):
    status: str
    preview_html: str | None = None
    results: list[dict] = []
    message: str = ""


@router.post("/quick-edit/theme", response_model=QuickEditStyleResponse)
async def quick_edit_theme(
    req: ThemeEditRequest,
    user: User = Depends(get_current_user),
):
    """Change the PPT theme colors and font scheme."""
    if not await _ensure_officecli():
        return JSONResponse(status_code=503, content={"installing": True, "detail": "OfficeCLI is being installed"})
    mgr = get_office_manager()

    docs_dir = _get_docs_dir(user.id, req.workspace)
    file_path = _safe_path(docs_dir, req.path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if file_path.suffix.lower() != ".pptx":
        raise HTTPException(status_code=400, detail="Theme editing is only supported for .pptx files")

    if not req.props:
        raise HTTPException(status_code=400, detail="No theme props provided")

    _backup_doc(file_path)

    logger.info("[QE-theme] %d prop(s) on %s", len(req.props), req.path)

    result = await mgr.set_props(str(file_path), "/theme", dict(req.props))

    results = [{
        "element": "/theme",
        "success": result.success,
        "error": result.error if not result.success else "",
    }]

    preview_html = await _render_preview_html(file_path)

    return QuickEditStyleResponse(
        status="ok" if result.success else "error",
        preview_html=preview_html,
        results=results,
        message="Theme updated" if result.success else f"Theme update failed: {result.error}",
    )


@router.post("/quick-edit/structure", response_model=StructureEditResponse)
async def quick_edit_structure(
    req: StructureEditRequest,
    user: User = Depends(get_current_user),
):
    """Run advanced structural editing (PPT / Word / Excel)."""
    if not await _ensure_officecli():
        return JSONResponse(status_code=503, content={"installing": True, "detail": "OfficeCLI is being installed"})

    mgr = get_office_manager()
    docs_dir = _get_docs_dir(user.id, req.workspace)
    file_path = _safe_path(docs_dir, req.path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    if ext not in _PREVIEWABLE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}'")

    if not req.operations:
        raise HTTPException(status_code=400, detail="No operations provided")

    _backup_doc(file_path)
    logger.info("[QE-structure] %d op(s) on %s", len(req.operations), req.path)

    results: list[dict] = []
    all_ok = True

    for idx, op in enumerate(req.operations, start=1):
        if not isinstance(op, dict):
            results.append({"index": idx, "success": False, "error": "Operation must be an object"})
            all_ok = False
            continue

        command = str(op.get("command", "")).strip().lower()
        try:
            if command == "set":
                path = str(op.get("path", "")).strip()
                props = op.get("props") or {}
                if not path or not isinstance(props, dict):
                    raise ValueError("set operation requires path and props object")
                result = await mgr.set_props(str(file_path), path, dict(props))
                target = path
            elif command == "add":
                parent = str(op.get("parent", "")).strip()
                element_type = str(op.get("type", "")).strip()
                props = op.get("props") or {}
                if not parent or not element_type:
                    raise ValueError("add operation requires parent and type")
                result = await mgr.add_element(str(file_path), parent, element_type, dict(props) if isinstance(props, dict) else {})
                target = f"{parent} -> {element_type}"
            elif command == "remove":
                path = str(op.get("path", "")).strip()
                if not path:
                    raise ValueError("remove operation requires path")
                result = await mgr.remove_element(str(file_path), path)
                target = path
            else:
                raise ValueError(f"Unsupported structure command: {command or '<empty>'}")

            results.append({
                "index": idx,
                "command": command,
                "target": target,
                "success": result.success,
                "error": result.error if not result.success else "",
            })
            if not result.success:
                all_ok = False
        except Exception as e:
            results.append({
                "index": idx,
                "command": command,
                "success": False,
                "error": str(e),
            })
            all_ok = False

    preview_html = await _render_preview_html(file_path)
    success_count = sum(1 for item in results if item.get("success"))
    status = "ok" if all_ok else "partial"
    return StructureEditResponse(
        status=status,
        preview_html=preview_html,
        results=results,
        message=f"{len(results)} operation(s), {success_count} succeeded",
    )

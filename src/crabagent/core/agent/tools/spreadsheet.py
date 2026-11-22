"""Spreadsheet tools: read .xlsx/.ods/.csv (and .xls via LibreOffice) with cell colours, and export to CSV."""
from __future__ import annotations

import csv
import os
from collections import Counter
from typing import Any

from crabagent.core.agent.tools.path_utils import resolve_tool_path
from crabagent.core.agent.tools.registry import registry
from crabagent.core.office.spreadsheet import (
    Cell,
    Sheet,
    SpreadsheetError,
    Workbook,
    col_letter,
    col_number,
    color_name,
    load_workbook,
)

_MAX_ROWS = 500
# Must stay under the agent loop's cut for a tool result (loop._MAX_TOOL_RESULT_CHARS,
# 20,000): past it the loop drops the end of the page, the "Next page" line with it, and
# the rows in between are silently lost.
_MAX_OUTPUT = 15_000
_MAX_VALUE = 200


def _labelled(color: str | None) -> str:
    return f"{color} {color_name(color)}" if color else ""


def _ranges(numbers: list[int], limit: int = 25) -> str:
    """[2, 3, 4, 8] -> "2-4, 8", shortened after *limit* pieces."""
    parts: list[str] = []
    start = prev = None
    for n in sorted(numbers):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            parts.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = n
    if start is not None:
        parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(parts[:limit]) + (f", … ({len(parts) - limit} more)" if len(parts) > limit else "")


def _show(value: object) -> str:
    if isinstance(value, bool):
        text = "TRUE" if value else "FALSE"
    elif value is None:
        text = ""
    else:
        text = str(value)
    text = text.replace("\r\n", "\n").replace("\n", " ⏎ ")
    return text if len(text) <= _MAX_VALUE else text[: _MAX_VALUE - 1] + "…"


def _row_format(sheet: Sheet, row: int) -> tuple[str | None, str | None, bool]:
    """The fill, font colour and bold that every non-empty cell of *row* shares, if any."""
    cells = [c for c in sheet.cells.get(row, {}).values() if c.value is not None]
    if not cells:
        return None, None, False
    fills = {c.fill for c in cells}
    fill = fills.pop() if len(fills) == 1 else None
    if fill is None and sheet.row_fills.get(row) and fills <= {None, sheet.row_fills[row]}:
        fill = sheet.row_fills[row]
    colors = {c.color for c in cells}
    color = colors.pop() if len(colors) == 1 else None
    return fill, color, all(c.bold for c in cells)


def _cell_notes(cell: Cell, row_fill: str | None, row_color: str | None, row_bold: bool, formulas: bool) -> str:
    notes = []
    if cell.fill and cell.fill != row_fill:
        notes.append(f"fill {_labelled(cell.fill)}")
    if cell.color and cell.color != row_color:
        notes.append(f"color {_labelled(cell.color)}")
    if cell.bold and not row_bold:
        notes.append("bold")
    if cell.italic:
        notes.append("italic")
    if cell.strike:
        notes.append("strike-through")
    if cell.comment:
        notes.append(f'note: "{_show(cell.comment)}"')
    if formulas and cell.formula:
        notes.append(cell.formula)
    return ", ".join(notes)


def _matches_fill(sheet: Sheet, row: int, wanted: str) -> bool:
    wanted = wanted.strip().lower()
    fills = {c.fill for c in sheet.cells.get(row, {}).values() if c.value is not None}
    if sheet.row_fills.get(row):
        fills.add(sheet.row_fills[row])
    fills.discard(None)
    if wanted == "any":
        return bool(fills)
    if wanted == "none":
        return not fills
    if wanted.startswith("#"):
        return wanted.upper() in fills
    # A colour name matches its shades: "red" finds "light red" and "dark red".
    return any(wanted == name or wanted in name.split() for name in (color_name(f) for f in fills))


def _pick_sheet(book: Workbook, sheet: str) -> tuple[Sheet | None, str]:
    if not book.sheets:
        return None, "The file has no sheets with cells."
    if not str(sheet).strip():
        return next((s for s in book.sheets if not s.hidden), book.sheets[0]), ""
    wanted = str(sheet).strip()
    for s in book.sheets:
        if s.name == wanted:
            return s, ""
    for s in book.sheets:
        if s.name.lower() == wanted.lower():
            return s, ""
    if wanted.isdigit() and 1 <= int(wanted) <= len(book.sheets):
        return book.sheets[int(wanted) - 1], ""
    names = ", ".join(f'"{s.name}"' for s in book.sheets)
    return None, f'No sheet called "{wanted}". Sheets: {names} (or use the sheet number, 1-{len(book.sheets)}).'


def _columns(spec: str, max_col: int) -> tuple[list[int] | None, str]:
    if not spec.strip():
        return list(range(1, max_col + 1)), ""
    cols: list[int] = []
    try:
        for part in spec.replace(" ", "").split(","):
            if ":" in part:
                lo, hi = part.split(":", 1)
                cols.extend(range(col_number(lo) if lo else 1, (col_number(hi) if hi else max_col) + 1))
            elif part:
                cols.append(col_number(part))
    except ValueError:
        return None, f'columns must be letters like "A:D" or "A,C,F", not "{spec}".'
    return sorted(set(cols)), ""


def _headers(sheet: Sheet, header_row: int) -> dict[int, str]:
    if header_row <= 0:
        return {}
    return {c: _show(cell.value) for c, cell in sheet.cells.get(header_row, {}).items() if cell.value is not None}


def _load(file_path: str, context: Any) -> tuple[Workbook | None, str]:
    path, err = resolve_tool_path(file_path, context)
    if err:
        return None, err
    try:
        return load_workbook(str(path)), ""
    except SpreadsheetError as exc:
        return None, f"Error: {exc}"
    except Exception as exc:  # a damaged file must not crash the agent
        return None, f"Error: could not read {path}: {type(exc).__name__}: {exc}"


def _overview(book: Workbook, header_row: int) -> str:
    lines = [f"{book.path} — {book.kind}, {len(book.sheets)} sheet(s)"]
    lines += [f"note: {n}" for n in book.notes]
    for i, sheet in enumerate(book.sheets, start=1):
        n_cells = sum(len(r) for r in sheet.cells.values())
        size = f"{sheet.max_row} rows x {sheet.max_col} columns" if n_cells else "empty"
        span = f" (A1:{col_letter(sheet.max_col)}{sheet.max_row})" if n_cells else ""
        lines.append(f'\nSheet {i} "{sheet.name}"{" [hidden sheet]" if sheet.hidden else ""}: {size}{span}, '
                     f"{n_cells:,} non-empty cells")
        if not n_cells:
            continue
        headers = _headers(sheet, header_row)
        if headers:
            shown = " | ".join(f"{col_letter(c)} {h[:40]}" for c, h in sorted(headers.items())[:30])
            lines.append(f"  header (row {header_row}): {shown}")
        if sheet.hidden_rows:
            lines.append(f"  hidden rows ({len(sheet.hidden_rows)}): {_ranges(list(sheet.hidden_rows))}")
        if sheet.hidden_cols:
            hidden = [col_letter(c) for c in sorted(sheet.hidden_cols) if c <= sheet.max_col]
            if hidden:
                lines.append(f"  hidden columns: {', '.join(hidden[:40])}")
        row_fills: dict[str, list[int]] = {}
        row_colors: dict[str, list[int]] = {}
        bold_rows: list[int] = []
        cell_fills: Counter[str] = Counter()
        cell_colors: Counter[str] = Counter()
        formulas = 0
        comments: list[str] = []
        for r in sorted(sheet.cells):
            fill, color, bold = _row_format(sheet, r)
            if fill:
                row_fills.setdefault(fill, []).append(r)
            if color:
                row_colors.setdefault(color, []).append(r)
            if bold:
                bold_rows.append(r)
            for c, cell in sheet.cells[r].items():
                if cell.fill and cell.fill != fill and cell.value is not None:
                    cell_fills[cell.fill] += 1
                if cell.color and cell.color != color and cell.value is not None:
                    cell_colors[cell.color] += 1
                formulas += bool(cell.formula)
                if cell.comment and len(comments) < 5:
                    comments.append(f'{col_letter(c)}{r}: "{_show(cell.comment)[:80]}"')
        for color, rows in sorted(row_fills.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  rows filled {_labelled(color)} ({len(rows)}): {_ranges(rows)}")
        for color, count in cell_fills.most_common(10):
            lines.append(f"  single cells filled {_labelled(color)}: {count}")
        for color, rows in sorted(row_colors.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  rows with text in {_labelled(color)} ({len(rows)}): {_ranges(rows)}")
        for color, count in cell_colors.most_common(10):
            lines.append(f"  single cells with text in {_labelled(color)}: {count}")
        if bold_rows:
            lines.append(f"  bold rows ({len(bold_rows)}): {_ranges(bold_rows)}")
        if sheet.merged:
            lines.append(f"  merged ({len(sheet.merged)}): {', '.join(sheet.merged[:12])}"
                         + (" …" if len(sheet.merged) > 12 else ""))
        if formulas:
            lines.append(f"  formulas: {formulas:,} cells (values shown are the last calculated results)")
        n_comments = sum(1 for row in sheet.cells.values() for cell in row.values() if cell.comment)
        if n_comments:
            lines.append(f"  notes/comments ({n_comments}): {'; '.join(comments)}")
        if sheet.conditional_formats:
            lines.append(f"  conditional formatting on {', '.join(sheet.conditional_formats[:10])}: "
                         "those colours depend on the values and are NOT included above")
        if sheet.truncated:
            lines.append("  only part of this sheet was read (it is very large)")
    lines.append('\nNext: mode="rows" to read rows (add fill="light red" to list only rows with that colour), '
                 "or spreadsheet_export to get a CSV with the colours as columns for analysis in code.")
    return "\n".join(lines)


def _rows(book: Workbook, sheet: Sheet, start_row: int, max_rows: int, columns: str, fill: str,
          formulas: bool, header_row: int) -> str:
    cols, err = _columns(columns, sheet.max_col)
    if cols is None:
        return f"Error: {err}"
    max_rows = max(1, min(int(max_rows or 50), _MAX_ROWS))
    first = int(start_row) if start_row else (header_row + 1 if header_row > 0 else 1)
    wanted = set(cols)
    candidates = [r for r in sorted(sheet.cells) if r >= first and any(c in wanted for c in sheet.cells[r])]
    if fill.strip():
        candidates = [r for r in candidates if _matches_fill(sheet, r, fill)]
    lines = [f'Sheet "{sheet.name}" ({sheet.max_row} rows, columns A-{col_letter(sheet.max_col)})'
             + (f' — rows whose fill matches "{fill}": {len(candidates)}' if fill.strip() else "")]
    lines += [f"note: {n}" for n in book.notes]
    headers = _headers(sheet, header_row)
    if headers and first > header_row:
        lines.append(f"header (row {header_row}): "
                     + " | ".join(f"{col_letter(c)} {headers[c][:40]}" for c in cols if c in headers))
    size = sum(len(line) + 1 for line in lines)
    shown = 0
    for r in candidates:
        if shown >= max_rows:
            break
        row_fill, row_color, row_bold = _row_format(sheet, r)
        tags = []
        if r in sheet.hidden_rows:
            tags.append("hidden")
        if row_fill:
            tags.append(f"fill {_labelled(row_fill)}")
        if row_color:
            tags.append(f"text {_labelled(row_color)}")
        if row_bold:
            tags.append("bold")
        parts = []
        for c in cols:
            cell = sheet.cells[r].get(c)
            if cell is None:
                continue
            notes = _cell_notes(cell, row_fill, row_color, row_bold, formulas)
            parts.append(f"{col_letter(c)}={_show(cell.value)}" + (f" {{{notes}}}" if notes else ""))
        line = f"{r}" + (f" [{', '.join(tags)}]" if tags else "") + ": " + " | ".join(parts)
        if shown and size + len(line) + 1 > _MAX_OUTPUT:
            break  # this row starts the next page
        lines.append(line)
        size += len(line) + 1
        shown += 1
    if not candidates:
        lines.append("(no rows" + (" with that fill" if fill.strip() else "") + f" from row {first})")
    elif shown < len(candidates):
        lines.append(f"Showing {shown} of {len(candidates)} rows. Next page: start_row={candidates[shown]}.")
    return "\n".join(lines)


@registry.register(
    name="spreadsheet_read",
    description=(
        "Read a spreadsheet: .xlsx/.xlsm (Excel), .ods (LibreOffice), .csv/.tsv; old .xls only when LibreOffice "
        "is installed. Shows values together with their formatting: fill (background) colour, text colour, bold, "
        "italic, strike-through, notes/comments, formulas, merged cells, hidden rows/columns/sheets. "
        "mode=overview (default) summarises every sheet: size, header row, which rows are coloured which colour, "
        "hidden rows, merged cells. mode=rows lists rows (paged), optionally only rows with a given fill colour "
        "(fill=\"light red\", \"yellow\", \"#FFC7CE\", \"any\", \"none\"). Colours are given as #RRGGBB plus a plain "
        "name. Read-only. Start with the overview."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute or workspace-relative path to the file."},
            "mode": {"type": "string", "enum": ["overview", "rows"], "default": "overview"},
            "sheet": {"type": "string", "description": "Sheet name or number (1 = first). Default: first visible."},
            "start_row": {
                "type": "integer",
                "description": "(rows) First row number to show. Default: the row after the header.",
            },
            "max_rows": {"type": "integer", "description": "(rows) Rows per page, up to 500. Default: 50.",
                         "default": 50},
            "columns": {"type": "string", "description": '(rows) Columns to show, e.g. "A:D" or "A,C,F".'},
            "fill": {
                "type": "string",
                "description": '(rows) Only rows with this fill: a colour name ("red" also matches "light red"), '
                '"#RRGGBB", "any" or "none".',
            },
            "formulas": {"type": "boolean", "description": "(rows) Also show each cell's formula.", "default": False},
            "header_row": {
                "type": "integer",
                "description": "Row holding the column names; 0 if there is none. Default: 1.",
                "default": 1,
            },
        },
        "required": ["file_path"],
    },
    metadata={"source": "builtin", "category": "file"},
)
def spreadsheet_read(
    file_path: str,
    mode: str = "overview",
    sheet: str = "",
    start_row: int = 0,
    max_rows: int = 50,
    columns: str = "",
    fill: str = "",
    formulas: bool = False,
    header_row: int = 1,
    context: Any = None,
) -> str:
    book, err = _load(file_path, context)
    if book is None:
        return err
    header_row = max(0, int(header_row if header_row is not None else 1))
    if mode == "rows":
        picked, err = _pick_sheet(book, sheet)
        if picked is None:
            return f"Error: {err}"
        return _rows(book, picked, start_row, max_rows, columns or "", fill or "", bool(formulas), header_row)
    if mode not in ("", "overview"):
        return f'Error: mode must be "overview" or "rows", not "{mode}".'
    if str(sheet or "").strip():
        picked, err = _pick_sheet(book, sheet)
        if picked is None:
            return f"Error: {err}"
        book = Workbook(path=book.path, kind=book.kind, sheets=[picked], notes=book.notes)
    return _overview(book, header_row)


@registry.register(
    name="spreadsheet_export",
    description=(
        "Export one sheet of a spreadsheet (.xlsx/.ods/.csv, .xls with LibreOffice) to a CSV file for analysis "
        "in code, with the formatting turned into columns: row (original row number), fill and fill_name "
        "(the row's background colour), text_color, bold, hidden, and cell_formats (cells formatted "
        "differently from their row), followed by the sheet's own columns named after the header row. "
        "Use it when a sheet is too big to read row by row or the analysis needs counting or grouping."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "The spreadsheet to export."},
            "output_path": {
                "type": "string",
                "description": "Where to write the CSV (absolute or workspace-relative).",
            },
            "sheet": {"type": "string", "description": "Sheet name or number (1 = first). Default: first visible."},
            "header_row": {
                "type": "integer",
                "description": "Row holding the column names; 0 if there is none (columns are then named A, B, ...). "
                "Default: 1.",
                "default": 1,
            },
            "overwrite": {"type": "boolean", "description": "Replace output_path if it exists.", "default": False},
        },
        "required": ["file_path", "output_path"],
    },
    metadata={"source": "builtin", "category": "file"},
)
def spreadsheet_export(
    file_path: str,
    output_path: str,
    sheet: str = "",
    header_row: int = 1,
    overwrite: bool = False,
    context: Any = None,
) -> str:
    book, err = _load(file_path, context)
    if book is None:
        return err
    picked, err = _pick_sheet(book, sheet)
    if picked is None:
        return f"Error: {err}"
    out, err = resolve_tool_path(output_path, context)
    if err:
        return err
    if os.path.abspath(str(out)) == os.path.abspath(book.path):
        return "Error: output_path is the spreadsheet itself; choose another file."
    if out.exists() and not overwrite:
        return f"Error: {out} already exists. Pass overwrite=true to replace it, or choose another output_path."
    header_row = max(0, int(header_row if header_row is not None else 1))
    headers = _headers(picked, header_row)
    data_cols = list(range(1, picked.max_col + 1))
    names: list[str] = []
    seen: set[str] = {"row", "fill", "fill_name", "text_color", "bold", "hidden", "cell_formats"}
    for c in data_cols:
        name = headers.get(c, "").strip() or col_letter(c)
        base, k = name, 2
        while name in seen:
            name, k = f"{base}_{k}", k + 1
        seen.add(name)
        names.append(name)
    fill_counts: Counter[str] = Counter()
    written = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["row", "fill", "fill_name", "text_color", "bold", "hidden", "cell_formats", *names])
        for r in sorted(picked.cells):
            if r <= header_row:
                continue
            cells = picked.cells[r]
            if not any(cell.value is not None for cell in cells.values()):
                continue
            fill, color, bold = _row_format(picked, r)
            formats = []
            for c in sorted(cells):
                notes = _cell_notes(cells[c], fill, color, bold, formulas=False)
                if notes:
                    formats.append(f"{col_letter(c)}: {notes}")
            values = []
            for c in data_cols:
                cell = cells.get(c)
                value = cell.value if cell is not None else None
                if isinstance(value, bool):
                    value = "TRUE" if value else "FALSE"
                values.append("" if value is None else value)
            writer.writerow([r, fill or "", color_name(fill), color or "", "yes" if bold else "",
                             "yes" if r in picked.hidden_rows else "", "; ".join(formats), *values])
            written += 1
            fill_counts[f"{fill} {color_name(fill)}" if fill else "no fill"] += 1
    summary = ", ".join(f"{name}: {n}" for name, n in fill_counts.most_common())
    return (f'Wrote {written} rows of sheet "{picked.name}" to {out} ({len(names)} data columns + 7 format columns).\n'
            f"Rows by fill: {summary or 'none'}.\n"
            f"Columns: row, fill, fill_name, text_color, bold, hidden, cell_formats, {', '.join(names[:30])}"
            + (" …" if len(names) > 30 else "")
            + ("\n" + "\n".join(f"note: {n}" for n in book.notes) if book.notes else ""))

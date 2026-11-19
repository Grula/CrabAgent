"""Read spreadsheets together with their cell formatting. Standard library only.

Supported: .xlsx/.xlsm/.xltx/.xltm (Excel), .ods/.fods (LibreOffice/OpenOffice), .csv/.tsv/.txt.
Old binary formats (.xls, .xlsb, .numbers, ...) are converted to .xlsx with LibreOffice when
it is installed.

For every non-empty cell this keeps the value plus what people use formatting for: fill
colour, font colour, bold, italic, strike-through, a comment/note and the formula. Per
sheet it also keeps merged ranges, hidden rows/columns, whether the sheet is hidden, and
where conditional formatting applies (its colours depend on the values and are not computed).

Colours are "#RRGGBB". A white fill and black text count as "no colour": they are what an
unformatted cell looks like.
"""
from __future__ import annotations

import colorsys
import csv
import datetime as dt
import io
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

MAX_CELLS = 3_000_000  # stop reading a sheet beyond this many non-empty cells
_MAX_REPEAT = 10_000  # cap on ODS repeated rows/columns that carry a value
_LIBREOFFICE_FORMATS = {".xls", ".xlsb", ".xlt", ".numbers", ".wk1", ".wks", ".dbf", ".sxc", ".uos"}
_XLSX_FORMATS = {".xlsx", ".xlsm", ".xltx", ".xltm"}
_ODS_FORMATS = {".ods", ".ots", ".fods"}


class SpreadsheetError(Exception):
    """The file cannot be read as a spreadsheet; the message says why and what to do."""


@dataclass
class Cell:
    value: object  # str, int, float, bool, or an ISO date/time string
    fill: str | None = None
    color: str | None = None
    bold: bool = False
    italic: bool = False
    strike: bool = False
    formula: str | None = None
    comment: str | None = None


@dataclass
class Sheet:
    name: str
    hidden: bool = False
    cells: dict[int, dict[int, Cell]] = field(default_factory=dict)  # row -> column -> cell, 1-based
    row_fills: dict[int, str] = field(default_factory=dict)  # fill given to a whole row (row style)
    hidden_rows: set[int] = field(default_factory=set)
    hidden_cols: set[int] = field(default_factory=set)
    merged: list[str] = field(default_factory=list)
    conditional_formats: list[str] = field(default_factory=list)  # ranges with conditional formatting
    truncated: bool = False
    _count: int = field(default=0, repr=False)

    @property
    def max_row(self) -> int:
        return max(self.cells, default=0)

    @property
    def max_col(self) -> int:
        return max((max(cols) for cols in self.cells.values() if cols), default=0)

    def put(self, row: int, col: int, cell: Cell) -> bool:
        """Store a cell; False once the sheet holds MAX_CELLS cells (the rest is not read)."""
        if self.truncated:
            return False
        self.cells.setdefault(row, {})[col] = cell
        self._count += 1
        if self._count >= MAX_CELLS:
            self.truncated = True
        return True


@dataclass
class Workbook:
    path: str
    kind: str  # "xlsx", "ods" or "csv"
    sheets: list[Sheet]
    notes: list[str] = field(default_factory=list)


# ── cell references ──────────────────────────────────────────────────────


def col_letter(col: int) -> str:
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def col_number(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        if not "A" <= ch <= "Z":
            raise ValueError(f"not a column: {letters!r}")
        n = n * 26 + ord(ch) - 64
    return n


_REF = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d+)")


def parse_ref(ref: str) -> tuple[int, int]:
    m = _REF.fullmatch(ref.strip())
    if not m:
        raise ValueError(f"not a cell reference: {ref!r}")
    return int(m.group(2)), col_number(m.group(1))


# ── colours ──────────────────────────────────────────────────────────────

# Excel's legacy colour palette (indexes 0-63); 64/65 are the system text/background colours.
_DEFAULT_PALETTE = (
    "000000 FFFFFF FF0000 00FF00 0000FF FFFF00 FF00FF 00FFFF "
    "000000 FFFFFF FF0000 00FF00 0000FF FFFF00 FF00FF 00FFFF "
    "800000 008000 000080 808000 800080 008080 C0C0C0 808080 "
    "9999FF 993366 FFFFCC CCFFFF 660066 FF8080 0066CC CCCCFF "
    "000080 FF00FF FFFF00 00FFFF 800080 800000 008080 0000FF "
    "00CCFF CCFFFF CCFFCC FFFF99 99CCFF FF99CC CC99FF FFCC99 "
    "3366FF 33CCCC 99CC00 FFCC00 FF9900 FF6600 666699 969696 "
    "003366 339966 003300 333300 993300 993366 333399 333333"
).split()

_HUES = ((15, "red"), (40, "orange"), (70, "yellow"), (165, "green"), (200, "cyan"), (255, "blue"),
         (290, "purple"), (345, "pink"), (360, "red"))


def color_name(hex_color: str | None) -> str:
    """A plain name for "#RRGGBB" that people use for highlights: "light red", "yellow", "gray"."""
    if not hex_color:
        return ""
    try:
        r, g, b = (int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return ""
    hue, light, _ = colorsys.rgb_to_hls(r, g, b)
    if max(r, g, b) - min(r, g, b) < 0.08:  # (almost) no colour: a shade of gray
        if light > 0.97:
            return "white"
        if light > 0.75:
            return "light gray"
        if light > 0.35:
            return "gray"
        return "dark gray" if light > 0.1 else "black"
    degrees = hue * 360
    name = next(n for limit, n in _HUES if degrees < limit)
    if name == "orange" and light < 0.35:
        return "brown"
    if light >= 0.7:
        return "light " + name
    if light < 0.3:
        return "dark " + name
    return name


def _hex(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lstrip("#")
    if len(value) == 8:  # ARGB
        value = value[2:]
    if len(value) != 6 or not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        return None
    return "#" + value.upper()


def _apply_tint(hex_color: str, tint: float) -> str:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    light = light * (1 + tint) if tint < 0 else light * (1 - tint) + tint
    r, g, b = colorsys.hls_to_rgb(hue, max(0.0, min(1.0, light)), sat)
    return "#" + "".join(f"{round(v * 255):02X}" for v in (r, g, b))


def _plain_fill(color: str | None) -> str | None:
    return None if color in (None, "#FFFFFF") else color


def _plain_font(color: str | None) -> str | None:
    return None if color in (None, "#000000") else color


# ── values ───────────────────────────────────────────────────────────────


def _number(text: str) -> int | float:
    value = float(text)
    if value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value


def _excel_serial_to_iso(serial: float, date1904: bool) -> str:
    if date1904:
        base = dt.datetime(1904, 1, 1)
    elif serial < 60:
        base = dt.datetime(1899, 12, 31)  # before Excel's made-up 1900-02-29
    else:
        base = dt.datetime(1899, 12, 30)
    moment = base + dt.timedelta(seconds=round(serial * 86400))
    if 0 <= serial < 1:
        return moment.strftime("%H:%M:%S")
    if moment.time() == dt.time(0):
        return moment.date().isoformat()
    return moment.isoformat(sep=" ")


_DATE_FORMAT_IDS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59)) | set(range(71, 82))


def _is_date_format(code: str | None) -> bool:
    if not code:
        return False
    code = re.sub(r'"[^"]*"', "", code)  # quoted text
    code = re.sub(r"\\.", "", code)  # escaped characters
    code = re.sub(r"\[(?![hms]+\])[^\]]*\]", "", code, flags=re.I)  # [Red], [$R$-416]; keeps [h]
    code = code.split(";")[0]
    return bool(re.search(r"[dmyhs]", code, re.I)) and code.strip().lower() != "general"


# ── XLSX ─────────────────────────────────────────────────────────────────


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str) -> str | None:
    """Attribute by local name, whatever its namespace (r:id, xr:uid, ...)."""
    value = el.get(name)
    if value is not None:
        return value
    for key, val in el.attrib.items():
        if _local(key) == name:
            return val
    return None


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def _kids(parent: ET.Element | None, container: str, name: str) -> list[ET.Element]:
    """Children called *name* of parent's child *container*, or [] when there is none."""
    box = _child(parent, container)
    return [] if box is None else _children(box, name)


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    if el is None:
        return None
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _truthy(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "on")


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    folder, name = posixpath.split(part)
    rels_path = posixpath.join(folder, "_rels", name + ".rels")
    try:
        root = ET.fromstring(z.read(rels_path))
    except KeyError:
        return {}
    out = {}
    for rel in root:
        target = rel.get("Target", "")
        if rel.get("TargetMode") == "External":
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id", "")] = (rel.get("Type", ""), path)
    return out


def _text_of(el: ET.Element) -> str:
    """Text of a shared string / comment: plain <t> or rich-text runs, without phonetic hints."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        for child in node:
            name = _local(child.tag)
            if name == "rPh":
                continue  # phonetic reading (Japanese), not part of the text
            if name == "t":
                parts.append(child.text or "")
            else:
                walk(child)

    walk(el)
    return "".join(parts)


class _XlsxStyles:
    def __init__(self, z: zipfile.ZipFile, styles_part: str | None, theme_part: str | None):
        self.theme: list[str | None] = []
        if theme_part:
            try:
                self._read_theme(ET.fromstring(z.read(theme_part)))
            except (KeyError, ET.ParseError):
                pass
        self.palette = list(_DEFAULT_PALETTE)
        self.num_formats: dict[int, str] = {}
        self.fonts: list[tuple[str | None, bool, bool, bool]] = []
        self.fills: list[str | None] = []
        self.xfs: list[tuple[int, int, int]] = []
        if not styles_part:
            return
        try:
            root = ET.fromstring(z.read(styles_part))
        except (KeyError, ET.ParseError):
            return
        colors = _child(root, "colors")
        custom = _child(colors, "indexedColors")
        if custom is not None:
            values = [_hex(c.get("rgb")) for c in _children(custom, "rgbColor")]
            for i, v in enumerate(values):
                if v and i < len(self.palette):
                    self.palette[i] = v.lstrip("#")
        for fmt in _kids(root, "numFmts", "numFmt"):
            try:
                self.num_formats[int(fmt.get("numFmtId", "-1"))] = fmt.get("formatCode", "")
            except ValueError:
                pass
        for font in _kids(root, "fonts", "font"):
            def flag(name: str) -> bool:
                el = _child(font, name)
                return el is not None and _truthy(el.get("val"))

            self.fonts.append((self.color(_child(font, "color")), flag("b"), flag("i"), flag("strike")))
        for fill in _kids(root, "fills", "fill"):
            self.fills.append(self._fill_color(fill))
        for xf in _kids(root, "cellXfs", "xf"):
            try:
                self.xfs.append((int(xf.get("numFmtId", 0)), int(xf.get("fontId", 0)), int(xf.get("fillId", 0))))
            except ValueError:
                self.xfs.append((0, 0, 0))

    def _read_theme(self, root: ET.Element) -> None:
        scheme = next((el for el in root.iter() if _local(el.tag) == "clrScheme"), None)
        if scheme is None:
            return
        by_name = {}
        for slot in scheme:
            spec = next(iter(slot), None)
            if spec is None:
                continue
            by_name[_local(slot.tag)] = _hex(spec.get("val") if _local(spec.tag) == "srgbClr" else spec.get("lastClr"))
        # Theme colour indexes put the light colour first: 0 = lt1, 1 = dk1, 2 = lt2, 3 = dk2.
        order = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6",
                 "hlink", "folHlink"]
        self.theme = [by_name.get(name) for name in order]

    def color(self, el: ET.Element | None) -> str | None:
        if el is None or _truthy(el.get("auto"), default=False):
            return None
        base = None
        if el.get("rgb"):
            base = _hex(el.get("rgb"))
        elif el.get("theme") is not None:
            try:
                idx = int(el.get("theme"))
                base = self.theme[idx] if idx < len(self.theme) else None
            except ValueError:
                base = None
        elif el.get("indexed") is not None:
            try:
                idx = int(el.get("indexed"))
                base = "#" + self.palette[idx] if idx < len(self.palette) else None
            except ValueError:
                base = None
        if base and el.get("tint"):
            try:
                base = _apply_tint(base, float(el.get("tint")))
            except ValueError:
                pass
        return base

    def _fill_color(self, fill: ET.Element) -> str | None:
        pattern = _child(fill, "patternFill")
        if pattern is not None:
            kind = pattern.get("patternType")
            if kind in (None, "none", "gray125"):
                return None
            return self.color(_child(pattern, "fgColor")) or self.color(_child(pattern, "bgColor"))
        gradient = _child(fill, "gradientFill")
        if gradient is not None:
            stop = _child(gradient, "stop")
            return self.color(_child(stop, "color")) if stop is not None else None
        return None

    def format_of(self, style: int) -> tuple[str | None, str | None, bool, bool, bool, bool]:
        """(fill, font colour, bold, italic, strike, is a date format) for cell style *style*."""
        if not 0 <= style < len(self.xfs):
            return None, None, False, False, False, False
        num_fmt, font_id, fill_id = self.xfs[style]
        fill = self.fills[fill_id] if 0 <= fill_id < len(self.fills) else None
        font = self.fonts[font_id] if 0 <= font_id < len(self.fonts) else (None, False, False, False)
        color, bold, italic, strike = font
        is_date = num_fmt in _DATE_FORMAT_IDS or _is_date_format(self.num_formats.get(num_fmt))
        return _plain_fill(fill), _plain_font(color), bold, italic, strike, is_date


def _read_xlsx(path: str) -> Workbook:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise SpreadsheetError(f"{os.path.basename(path)} is not a valid .xlsx file ({exc}).") from exc
    with z:
        root_rels = _rels(z, "")
        wb_part = next((p for t, p in root_rels.values() if t.endswith("/officeDocument")), "xl/workbook.xml")
        try:
            wb_root = ET.fromstring(z.read(wb_part))
        except KeyError as exc:
            raise SpreadsheetError(f"{os.path.basename(path)} has no workbook part; it is not an Excel file.") from exc
        wb_rels = _rels(z, wb_part)

        def part_of(suffix: str) -> str | None:
            return next((p for t, p in wb_rels.values() if t.endswith(suffix)), None)

        styles = _XlsxStyles(z, part_of("/styles"), part_of("/theme"))
        shared: list[str] = []
        ss_part = part_of("/sharedStrings")
        if ss_part:
            try:
                for _, el in ET.iterparse(z.open(ss_part)):
                    if _local(el.tag) == "si":
                        shared.append(_text_of(el))
                        el.clear()
            except KeyError:
                pass
        pr = _child(wb_root, "workbookPr")
        date1904 = pr is not None and _truthy(pr.get("date1904"), default=False)
        sheets = []
        for sheet_el in _kids(wb_root, "sheets", "sheet"):
            rel = wb_rels.get(_attr(sheet_el, "id") or "")
            if not rel or not rel[0].endswith("/worksheet"):
                continue  # chart sheets and dialog sheets hold no cells
            sheet = Sheet(name=sheet_el.get("name", "?"), hidden=sheet_el.get("state", "visible") != "visible")
            _read_xlsx_sheet(z, rel[1], sheet, styles, shared, date1904)
            sheets.append(sheet)
    return Workbook(path=path, kind="xlsx", sheets=sheets)


def _read_xlsx_sheet(z, part, sheet: Sheet, styles: _XlsxStyles, shared: list[str], date1904: bool) -> None:
    comments: dict[tuple[int, int], str] = {}
    for rel_type, target in _rels(z, part).values():
        if rel_type.endswith("/comments"):
            try:
                root = ET.fromstring(z.read(target))
            except (KeyError, ET.ParseError):
                continue
            for c in root.iter():
                if _local(c.tag) == "comment" and c.get("ref"):
                    try:
                        comments[parse_ref(c.get("ref"))] = _text_of(c).strip()
                    except ValueError:
                        pass
    row_num = col_num = 0
    for event, el in ET.iterparse(z.open(part), events=("start", "end")):
        name = _local(el.tag)
        if event == "start":
            if name == "row":
                try:
                    row_num = int(el.get("r") or row_num + 1)
                except ValueError:
                    row_num += 1
                col_num = 0
                if _truthy(el.get("hidden"), default=False):
                    sheet.hidden_rows.add(row_num)
                if (el.get("s") or "").isdigit() and _truthy(el.get("customFormat"), default=False):
                    fill = styles.format_of(int(el.get("s")))[0]
                    if fill:
                        sheet.row_fills[row_num] = fill
            continue
        if name == "c":
            ref = el.get("r")
            try:
                r, c = parse_ref(ref) if ref else (row_num, col_num + 1)
            except ValueError:
                r, c = row_num, col_num + 1
            col_num = c
            try:
                style = int(el.get("s") or 0)
            except ValueError:
                style = 0
            fill, color, bold, italic, strike, is_date = styles.format_of(style)
            kind = el.get("t", "n")
            v = _child(el, "v")
            f = _child(el, "f")
            text = v.text if v is not None else None
            value: object = None
            if kind == "inlineStr":
                inline = _child(el, "is")
                value = _text_of(inline) if inline is not None else None
            elif text is None:
                value = None
            elif kind == "s":
                try:
                    value = shared[int(text)]
                except (ValueError, IndexError):
                    value = text
            elif kind == "b":
                value = text.strip() in ("1", "true")
            elif kind in ("str", "e", "d"):
                value = text
            else:
                try:
                    value = _excel_serial_to_iso(float(text), date1904) if is_date else _number(text)
                except (ValueError, OverflowError):
                    value = text
            comment = comments.pop((r, c), None)
            if value not in (None, "") or comment:
                formula = f.text if f is not None and f.text else None
                cell = Cell(value=value, fill=fill, color=color, bold=bold, italic=italic, strike=strike,
                            formula=("=" + formula) if formula else None, comment=comment)
                if not sheet.put(r, c, cell):
                    break
            el.clear()
        elif name == "row":
            el.clear()
        elif name == "col":
            if _truthy(el.get("hidden"), default=False):
                try:
                    lo, hi = int(el.get("min", 0)), int(el.get("max", 0))
                    sheet.hidden_cols.update(range(lo, min(hi, 16384) + 1))
                except ValueError:
                    pass
        elif name == "mergeCell" and el.get("ref"):
            sheet.merged.append(el.get("ref"))
        elif name == "conditionalFormatting" and _attr(el, "sqref"):
            sheet.conditional_formats.append(_attr(el, "sqref"))
    for (r, c), text in comments.items():  # notes on empty cells
        sheet.put(r, c, Cell(value=None, comment=text))


# ── ODS ──────────────────────────────────────────────────────────────────

_NS_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_NS_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_NS_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_NS_STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
_NS_FO = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"


def _t(name: str) -> str:
    return f"{{{_NS_TABLE}}}{name}"


def _ods_styles(*roots: ET.Element) -> dict[tuple[str, str], dict]:
    raw: dict[tuple[str, str], dict] = {}
    for root in roots:
        for st in root.iter(f"{{{_NS_STYLE}}}style"):
            family, name = st.get(f"{{{_NS_STYLE}}}family", ""), st.get(f"{{{_NS_STYLE}}}name", "")
            props: dict = {"parent": st.get(f"{{{_NS_STYLE}}}parent-style-name")}
            cell_props = st.find(f"{{{_NS_STYLE}}}table-cell-properties")
            if cell_props is not None and cell_props.get(f"{{{_NS_FO}}}background-color"):
                bg = cell_props.get(f"{{{_NS_FO}}}background-color")
                props["fill"] = "none" if bg == "transparent" else (_hex(bg) or "none")
            text_props = st.find(f"{{{_NS_STYLE}}}text-properties")
            if text_props is not None:
                if text_props.get(f"{{{_NS_FO}}}color"):
                    props["color"] = _hex(text_props.get(f"{{{_NS_FO}}}color"))
                weight = text_props.get(f"{{{_NS_FO}}}font-weight")
                if weight:
                    props["bold"] = weight == "bold" or (weight.isdigit() and int(weight) >= 600)
                style = text_props.get(f"{{{_NS_FO}}}font-style")
                if style:
                    props["italic"] = style in ("italic", "oblique")
                strike = text_props.get(f"{{{_NS_STYLE}}}text-line-through-style")
                if strike:
                    props["strike"] = strike != "none"
            table_props = st.find(f"{{{_NS_STYLE}}}table-properties")
            if table_props is not None and table_props.get(f"{{{_NS_TABLE}}}display") == "false":
                props["hidden"] = True
            raw[(family, name)] = props
    resolved: dict[tuple[str, str], dict] = {}

    def resolve(key: tuple[str, str], depth: int = 0) -> dict:
        if key in resolved:
            return resolved[key]
        props = raw.get(key, {})
        parent = props.get("parent")
        merged = dict(resolve((key[0], parent), depth + 1)) if parent and depth < 20 else {}
        merged.update({k: v for k, v in props.items() if k != "parent"})
        resolved[key] = merged
        return merged

    for key in raw:
        resolve(key)
    return resolved


def _ods_text(el: ET.Element) -> str:
    parts = [el.text or ""]
    for child in el:
        tag = child.tag
        if tag == f"{{{_NS_TEXT}}}s":
            parts.append(" " * int(child.get(f"{{{_NS_TEXT}}}c", "1")))
        elif tag == f"{{{_NS_TEXT}}}tab":
            parts.append("\t")
        elif tag == f"{{{_NS_TEXT}}}line-break":
            parts.append("\n")
        elif tag != f"{{{_NS_OFFICE}}}annotation":
            parts.append(_ods_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _ods_value(cell: ET.Element) -> object:
    kind = cell.get(f"{{{_NS_OFFICE}}}value-type")
    if kind in ("float", "percentage", "currency"):
        try:
            return _number(cell.get(f"{{{_NS_OFFICE}}}value", ""))
        except ValueError:
            pass
    elif kind == "date":
        value = cell.get(f"{{{_NS_OFFICE}}}date-value", "")
        if value:
            return value[:-9] if value.endswith("T00:00:00") else value.replace("T", " ")
    elif kind == "time":
        m = re.fullmatch(r"-?PT(\d+)H(\d+)M(\d+)(?:[.,]\d+)?S", cell.get(f"{{{_NS_OFFICE}}}time-value", ""))
        if m:
            return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}:{int(m.group(3)):02d}"
    elif kind == "boolean":
        return cell.get(f"{{{_NS_OFFICE}}}boolean-value") == "true"
    paragraphs = [_ods_text(p) for p in cell if p.tag == f"{{{_NS_TEXT}}}p"]
    if paragraphs:
        return "\n".join(paragraphs)
    return cell.get(f"{{{_NS_OFFICE}}}string-value")


def _iter_ods(el: ET.Element, leaf: str, groups: tuple[str, ...]):
    for child in el:
        if child.tag == _t(leaf):
            yield child
        elif child.tag in tuple(_t(g) for g in groups):
            yield from _iter_ods(child, leaf, groups)


def _read_ods(path: str) -> Workbook:
    try:
        if path.lower().endswith(".fods"):
            root = ET.parse(path).getroot()
            content, styles_root = root, root
        else:
            with zipfile.ZipFile(path) as z:
                content = ET.fromstring(z.read("content.xml"))
                try:
                    styles_root = ET.fromstring(z.read("styles.xml"))
                except KeyError:
                    styles_root = ET.Element("none")
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise SpreadsheetError(f"{os.path.basename(path)} is not a valid OpenDocument spreadsheet ({exc}).") from exc
    styles = _ods_styles(styles_root, content)
    sheets = [_read_ods_table(table, styles) for table in content.iter(_t("table"))]
    return Workbook(path=path, kind="ods", sheets=sheets)


def _read_ods_table(table: ET.Element, styles: dict) -> Sheet:
    table_style = styles.get(("table", table.get(_t("style-name"), "")), {})
    sheet = Sheet(name=table.get(_t("name"), "?"), hidden=bool(table_style.get("hidden")))
    col_styles: list[tuple[int, int, str]] = []  # (first, last, default cell style)
    col = 0
    for col_el in _iter_ods(table, "table-column", ("table-column-group", "table-header-columns", "table-columns")):
        repeat = int(col_el.get(_t("number-columns-repeated"), "1"))
        if col_el.get(_t("visibility")) in ("collapse", "filter"):
            sheet.hidden_cols.update(range(col + 1, min(col + repeat, 16384) + 1))
        default = col_el.get(_t("default-cell-style-name"))
        if default:
            col_styles.append((col + 1, col + repeat, default))
        col += repeat

    def default_style(c: int) -> str | None:
        for first, last, name in col_styles:
            if first <= c <= last:
                return name
        return None

    hidden_ranges: list[tuple[int, int]] = []
    row = 0
    for row_el in _iter_ods(table, "table-row", ("table-row-group", "table-header-rows", "table-rows")):
        row_repeat = int(row_el.get(_t("number-rows-repeated"), "1"))
        if row_el.get(_t("visibility")) in ("collapse", "filter"):
            hidden_ranges.append((row + 1, row + row_repeat))
        cells: list[tuple[int, Cell]] = []
        col = 0
        for cell_el in row_el:
            tag = cell_el.tag
            if tag not in (_t("table-cell"), _t("covered-table-cell")):
                continue
            repeat = int(cell_el.get(_t("number-columns-repeated"), "1"))
            if tag == _t("table-cell"):
                value = _ods_value(cell_el)
                note = cell_el.find(f"{{{_NS_OFFICE}}}annotation")
                comment = None
                if note is not None:
                    comment = "\n".join(_ods_text(p) for p in note.iter(f"{{{_NS_TEXT}}}p")).strip()
                if value not in (None, "") or comment:
                    style_name = cell_el.get(_t("style-name")) or default_style(col + 1) or ""
                    props = styles.get(("table-cell", style_name), {})
                    fill = props.get("fill")
                    formula = cell_el.get(_t("formula"))
                    if formula:
                        formula = re.sub(r"^(of|ooow|msoxl):", "", formula)
                    for k in range(min(repeat, _MAX_REPEAT)):
                        cells.append((col + 1 + k, Cell(
                            value=value if value != "" else None,
                            fill=_plain_fill(None if fill == "none" else fill),
                            color=_plain_font(props.get("color")),
                            bold=bool(props.get("bold")), italic=bool(props.get("italic")),
                            strike=bool(props.get("strike")), formula=formula, comment=comment,
                        )))
                    span_c = int(cell_el.get(_t("number-columns-spanned"), "1"))
                    span_r = int(cell_el.get(_t("number-rows-spanned"), "1"))
                    if span_c > 1 or span_r > 1:
                        sheet.merged.append(
                            f"{col_letter(col + 1)}{row + 1}:{col_letter(col + span_c)}{row + span_r}"
                        )
            col += repeat
        for k in range(min(row_repeat, _MAX_REPEAT) if cells else 0):
            if not all(sheet.put(row + 1 + k, c, cell) for c, cell in cells):
                break
        row += row_repeat
        if sheet.truncated:
            break
    last = sheet.max_row
    for first, end in hidden_ranges:
        sheet.hidden_rows.update(range(first, min(end, last) + 1))
    return sheet


# ── CSV ──────────────────────────────────────────────────────────────────


def _read_csv(path: str) -> Workbook:
    with open(path, "rb") as fh:
        raw = fh.read()
    for bom, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            text = raw.decode(encoding)
            break
    else:
        try:
            text, encoding = raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            try:
                text, encoding = raw.decode("cp1252"), "cp1252"
            except UnicodeDecodeError:
                text, encoding = raw.decode("latin-1"), "latin-1"
    if path.lower().endswith((".tsv", ".tab")):
        delimiter = "\t"
    else:
        sample = text[:65536]
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            first_lines = sample.splitlines()[:20]
            delimiter = max(",;\t|", key=lambda d: sum(line.count(d) for line in first_lines))
    sheet = Sheet(name=os.path.splitext(os.path.basename(path))[0])
    csv.field_size_limit(max(csv.field_size_limit(), 50_000_000))  # long text cells
    try:
        for r, record in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter), start=1):
            for c, value in enumerate(record, start=1):
                if value != "" and not sheet.put(r, c, Cell(value=value)):
                    break
            if sheet.truncated:
                break
    except csv.Error as exc:
        raise SpreadsheetError(f"{os.path.basename(path)} could not be read as CSV ({exc}).") from exc
    names = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}
    return Workbook(path=path, kind="csv", sheets=[sheet],
                    notes=[f"CSV read as {encoding}, separated by {names.get(delimiter, repr(delimiter))}; "
                           "CSV has no formatting, so there are no colours"])


# ── entry point ──────────────────────────────────────────────────────────


def _convert_with_libreoffice(path: str) -> tuple[str, str]:
    """Convert an old binary spreadsheet to .xlsx; returns (xlsx path, temp folder to delete)."""
    office = shutil.which("soffice") or shutil.which("libreoffice")
    ext = os.path.splitext(path)[1].lower()
    if not office:
        raise SpreadsheetError(
            f"{ext} files (old binary format) can only be read after converting them, and LibreOffice is not "
            "installed. Open the file and save it as .xlsx or .ods, or install LibreOffice "
            "(e.g. `sudo apt install libreoffice-calc`) and try again."
        )
    out_dir = tempfile.mkdtemp(prefix="crabagent-sheet-")
    try:
        subprocess.run(
            [office, "--headless", "--convert-to", "xlsx", "--outdir", out_dir, path],
            capture_output=True, timeout=180, check=False,
        )
        converted = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".xlsx")
        if not os.path.isfile(converted):
            raise SpreadsheetError(f"LibreOffice could not convert {os.path.basename(path)} to .xlsx.")
        return converted, out_dir
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise


def _detect_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                if "content.xml" in names:
                    return "ods"
                if any(n.startswith("xl/") for n in names):
                    return "xlsx"
        except zipfile.BadZipFile:
            pass
        raise SpreadsheetError(f"{os.path.basename(path)} is a zip file but not an .xlsx or .ods spreadsheet.")
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        if ext in _XLSX_FORMATS:
            raise SpreadsheetError(
                f"{os.path.basename(path)} is password-protected (encrypted); remove the password and try again."
            )
        return "libreoffice"
    if ext in _LIBREOFFICE_FORMATS:
        return "libreoffice"
    if ext == ".fods" or (head.lstrip().startswith(b"<?xml") and ext in _ODS_FORMATS):
        return "ods"
    return "csv"


def read_workbook(path: str) -> Workbook:
    if not os.path.isfile(path):
        raise SpreadsheetError(f"File not found: {path}")
    kind = _detect_kind(path)
    if kind == "xlsx":
        return _read_xlsx(path)
    if kind == "ods":
        return _read_ods(path)
    if kind == "csv":
        return _read_csv(path)
    converted, tmp_dir = _convert_with_libreoffice(path)
    try:
        book = _read_xlsx(converted)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    book.path = path
    book.notes.append(f"converted from {os.path.splitext(path)[1]} to .xlsx with LibreOffice")
    return book


_CACHE: OrderedDict[tuple[str, float, int], Workbook] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def load_workbook(path: str) -> Workbook:
    """read_workbook with a small cache, so paging through a big file parses it once."""
    path = os.path.abspath(path)
    try:
        stat = os.stat(path)
    except OSError as exc:
        raise SpreadsheetError(f"File not found: {path}") from exc
    key = (path, stat.st_mtime, stat.st_size)
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    book = read_workbook(path)
    with _CACHE_LOCK:
        _CACHE[key] = book
        while len(_CACHE) > 4:
            _CACHE.popitem(last=False)
    return book

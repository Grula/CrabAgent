#!/usr/bin/env python3
"""Audit the formulas in an .xlsx/.xlsm workbook.

Usage:
    python check_formulas.py book.xlsx [--recalc] [--write-back] [--sheet NAME]
                             [--json] [--limit 25]

What it reports
  ERRORS    formula cells whose cached value is an Excel error (#REF!, #DIV/0!, ...)
  NO VALUE  formula cells with no cached value (written by openpyxl and never
            recalculated - pandas / data_only readers and previewers see blanks)
  COMPAT    functions that need a recent Excel (dynamic arrays, LAMBDA, ...) or
            that must be written with the _xlfn. prefix when saved by openpyxl
  EXTERNAL  references into other workbooks ([1]Sheet!A1) - fragile on re-save
  OUTLIER   a formula that differs from both neighbours in its row while those
            neighbours agree with each other (the classic "one cell was edited
            by hand" bug in a projection row)
  LITERALS  numeric constants embedded in formulas (candidates for an input cell)

--recalc      Recalculate a temporary copy with LibreOffice (headless, throwaway
              profile configured to always recalculate on load) and check the
              values of that copy. The original file is not modified.
--write-back  With --recalc: replace the original with the recalculated copy so
              cached values are stored in the file. LibreOffice may drop
              features it does not support; keep a backup when the workbook has
              macros, slicers, or other advanced Excel features.

Exit code: 0 = no errors found, 1 = error values present (or recalc failed),
2 = bad usage / unreadable file.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ERROR_VALUES = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!", "#SPILL!", "#CALC!")

# Functions that only exist in Excel 365/2021+ (dynamic arrays etc.). They break in
# older Excel, LibreOffice (partly) and most non-Excel readers.
MODERN_FUNCS = {
    "XLOOKUP", "XMATCH", "FILTER", "SORT", "SORTBY", "UNIQUE", "SEQUENCE", "RANDARRAY",
    "LET", "LAMBDA", "MAP", "REDUCE", "SCAN", "MAKEARRAY", "BYROW", "BYCOL", "VSTACK",
    "HSTACK", "TAKE", "DROP", "CHOOSECOLS", "CHOOSEROWS", "TEXTSPLIT", "TEXTBEFORE",
    "TEXTAFTER", "TOCOL", "TOROW", "WRAPROWS", "WRAPCOLS", "EXPAND", "GROUPBY", "PIVOTBY",
}
# Post-2007 functions that work broadly but must be stored with the _xlfn. prefix
# when a library (openpyxl) writes the formula text verbatim.
XLFN_FUNCS = {
    "TEXTJOIN", "CONCAT", "IFS", "SWITCH", "MAXIFS", "MINIFS", "IFNA", "DAYS", "ISOWEEKNUM",
    "STDEV.S", "STDEV.P", "VAR.S", "VAR.P", "PERCENTILE.INC", "PERCENTILE.EXC", "QUARTILE.INC",
    "QUARTILE.EXC", "RANK.EQ", "RANK.AVG", "MODE.SNGL", "NORM.DIST", "NORM.INV", "NORM.S.DIST",
    "NORM.S.INV", "T.TEST", "T.DIST", "T.INV", "CEILING.MATH", "FLOOR.MATH", "AGGREGATE",
    "FORECAST.LINEAR", "XOR", "SHEET", "SHEETS", "FORMULATEXT", "ISFORMULA", "NUMBERVALUE",
}
ALLOWED_LITERALS = {"0", "1", "2", "3", "4", "7", "10", "12", "24", "30", "52", "60", "100", "365", "1000"}

FUNC_RE = re.compile(r"(?<![A-Za-z0-9_.])(_xlfn\.)?([A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)*)\s*\(")
STRING_RE = re.compile(r'"(?:[^"]|"")*"')
SHEET_PREFIX_RE = re.compile(r"(?:'(?:[^']|'')+'|\[[^\]]+\][A-Za-z0-9_.]+|[A-Za-z0-9_.]+)!")
REF_RE = re.compile(r"(?<![A-Za-z0-9_.$])(\$?)([A-Z]{1,3})(\$?)([0-9]{1,7})(?![0-9A-Za-z_(])")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.$:])(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![0-9A-Za-z_(])")


def col_to_num(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


def formula_text(value) -> str | None:
    """Return the formula string for a cell value, or None if it is not a formula."""
    if isinstance(value, str) and value.startswith("="):
        return value
    text = getattr(value, "text", None)  # openpyxl ArrayFormula / DataTableFormula
    if isinstance(text, str):
        return text if text.startswith("=") else "=" + text
    return None


def relative_form(formula: str, row: int, col: int) -> str:
    """Rewrite A1 references as offsets from (row, col) so copied formulas compare equal."""
    no_strings = STRING_RE.sub('""', formula)

    def repl(m: re.Match) -> str:
        col_abs, col_s, row_abs, row_s = m.groups()
        c = f"C{col_s}" if col_abs else f"C[{col_to_num(col_s) - col}]"
        r = f"R{row_s}" if row_abs else f"R[{int(row_s) - row}]"
        return r + c

    return REF_RE.sub(repl, no_strings)


def literals_in(formula: str) -> list[str]:
    body = STRING_RE.sub('""', formula)
    body = SHEET_PREFIX_RE.sub("", body)
    body = REF_RE.sub("", body)
    body = re.sub(r"\b[A-Z]{1,3}:[A-Z]{1,3}\b|\b\d+:\d+\b", "", body)  # whole-column/row ranges
    return [n for n in NUMBER_RE.findall(body) if n not in ALLOWED_LITERALS]


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        if shutil.which(name):
            return shutil.which(name)
    for candidate in (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        "/usr/lib/libreoffice/program/soffice",
    ):
        if Path(candidate).exists():
            return candidate
    return None


RECALC_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""


def recalc_copy(src: Path, workdir: Path) -> Path:
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) is not installed; cannot recalculate. "
                           "Install it, or rebuild the formulas with office_edit (its engine stores computed values).")
    profile = workdir / "profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user" / "registrymodifications.xcu").write_text(RECALC_XCU, encoding="utf-8")
    outdir = workdir / "out"
    outdir.mkdir()
    filt = "xlsx:Calc MS Excel 2007 XML"
    cmd = [soffice, "--headless", "--norestore", f"-env:UserInstallation={profile.resolve().as_uri()}",
           "--convert-to", filt, "--outdir", str(outdir), str(src)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    out = outdir / (src.stem + ".xlsx")
    if not out.exists():
        raise RuntimeError(f"LibreOffice recalculation failed:\n{proc.stdout}\n{proc.stderr}")
    return out


def audit(formula_path: Path, value_path: Path, only_sheet: str | None) -> dict:
    from openpyxl import load_workbook

    keep_vba = formula_path.suffix.lower() == ".xlsm"
    wb_f = load_workbook(formula_path, data_only=False, keep_vba=keep_vba)
    wb_v = load_workbook(value_path, data_only=True)
    report = {
        "file": str(formula_path),
        "total_formulas": 0,
        "errors": defaultdict(list),
        "no_cached_value": [],
        "compat": defaultdict(list),
        "needs_xlfn_prefix": defaultdict(list),
        "external_refs": [],
        "row_outliers": [],
        "literals": [],
    }
    for ws in wb_f.worksheets:
        if only_sheet and ws.title != only_sheet:
            continue
        ws_v = wb_v[ws.title] if ws.title in wb_v.sheetnames else None
        rows: dict[int, dict[int, str]] = defaultdict(dict)
        for row in ws.iter_rows():
            for cell in row:
                f = formula_text(cell.value)
                if f is None:
                    continue
                report["total_formulas"] += 1
                where = f"'{ws.title}'!{cell.coordinate}"
                rows[cell.row][cell.column] = relative_form(f, cell.row, cell.column)

                cached = ws_v[cell.coordinate].value if ws_v is not None else None
                if isinstance(cached, str) and cached.strip() in ERROR_VALUES:
                    report["errors"][cached.strip()].append(where)
                elif cached is None:
                    report["no_cached_value"].append(where)

                upper = STRING_RE.sub('""', f).upper()
                for prefix, name in FUNC_RE.findall(upper):
                    if name in MODERN_FUNCS:
                        report["compat"][name].append(where)
                    elif name in XLFN_FUNCS and not prefix:
                        report["needs_xlfn_prefix"][name].append(where)
                if re.search(r"\[\d+\]", STRING_RE.sub('""', f)):
                    report["external_refs"].append(where)
                lits = literals_in(f)
                if lits:
                    report["literals"].append(f"{where} {f}  ->  {', '.join(lits)}")

        for r, cols in rows.items():
            for c, form in cols.items():
                left, right = cols.get(c - 1), cols.get(c + 1)
                if left and right and left == right and form != left:
                    report["row_outliers"].append(f"'{ws.title}'!{ws.cell(row=r, column=c).coordinate}")
    report["errors"] = dict(report["errors"])
    report["compat"] = dict(report["compat"])
    report["needs_xlfn_prefix"] = dict(report["needs_xlfn_prefix"])
    report["total_errors"] = sum(len(v) for v in report["errors"].values())
    return report


def print_report(rep: dict, limit: int, recalculated: bool) -> None:
    def show(items: list[str]) -> str:
        more = f" ... (+{len(items) - limit} more)" if len(items) > limit else ""
        return ", ".join(items[:limit]) + more

    print(f"Workbook: {rep['file']}")
    print(f"Formulas: {rep['total_formulas']}   Error cells: {rep['total_errors']}"
          f"   Values checked from: {'recalculated copy' if recalculated else 'cached values in file'}")
    if rep["errors"]:
        print("\nERRORS")
        for err, cells in rep["errors"].items():
            print(f"  {err:8} x{len(cells)}: {show(cells)}")
    if rep["no_cached_value"]:
        print(f"\nNO VALUE  {len(rep['no_cached_value'])} formula(s) have no cached result "
              f"(run with --recalc, or verify via office_read): {show(rep['no_cached_value'])}")
    if rep["compat"]:
        print("\nCOMPAT  (Excel 365-only functions; replace unless the user only uses current Excel)")
        for name, cells in rep["compat"].items():
            print(f"  {name}: {show(cells)}")
    if rep["needs_xlfn_prefix"]:
        print("\nCOMPAT  (write as _xlfn.NAME when saving with openpyxl, or Excel shows #NAME?)")
        for name, cells in rep["needs_xlfn_prefix"].items():
            print(f"  {name}: {show(cells)}")
    if rep["external_refs"]:
        print(f"\nEXTERNAL  links to other workbooks: {show(rep['external_refs'])}")
    if rep["row_outliers"]:
        print(f"\nOUTLIER  formula breaks the pattern of its row neighbours: {show(rep['row_outliers'])}")
    if rep["literals"]:
        print(f"\nLITERALS  hardcoded numbers inside formulas (move assumptions to labelled input cells):")
        for line in rep["literals"][:limit]:
            print(f"  {line}")
        if len(rep["literals"]) > limit:
            print(f"  ... (+{len(rep['literals']) - limit} more)")
    if not any([rep["errors"], rep["no_cached_value"], rep["compat"], rep["needs_xlfn_prefix"],
                rep["external_refs"], rep["row_outliers"], rep["literals"]]):
        print("\nOK - no issues found.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workbook")
    ap.add_argument("--recalc", action="store_true")
    ap.add_argument("--write-back", action="store_true")
    ap.add_argument("--sheet")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("ERROR: openpyxl is required (pip install openpyxl)", file=sys.stderr)
        return 2

    path = Path(args.workbook).expanduser().resolve()
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="xlsx_check_") as tmp:
        value_path = path
        if args.recalc:
            try:
                value_path = recalc_copy(path, Path(tmp))
            except Exception as exc:  # noqa: BLE001
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
        try:
            rep = audit(path, value_path, args.sheet)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: could not read workbook: {exc}", file=sys.stderr)
            return 2
        if args.recalc and args.write_back:
            if path.suffix.lower() != ".xlsx":
                print("WARNING: --write-back skipped (only .xlsx; .xlsm macros would be lost)", file=sys.stderr)
            else:
                shutil.copyfile(value_path, path)
                rep["written_back"] = True

    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print_report(rep, args.limit, args.recalc)
        if rep.get("written_back"):
            print("\nRecalculated values written back to the original file.")
    return 1 if rep["total_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())

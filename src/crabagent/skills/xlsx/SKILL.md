---
name: xlsx
description: Create, edit, analyze and fix Excel workbooks (.xlsx/.xlsm/.csv) with live formulas, number formats, charts, pivots and financial-model conventions, then recalculate and check formula errors. Use whenever a spreadsheet is the input or deliverable (budget, model, tracker, CSV to Excel).
---

# Spreadsheets (.xlsx)

Scripts and references below are relative to the skill directory shown when this skill loads.

| Task | First choice | Fallback / bulk |
|---|---|---|
| Look at a workbook | `office_read` (text mode lists sheets, values and formula cells with computed results; `sheet`, `cols`, `offset` to page) | `pandas.read_excel`, openpyxl |
| Build a sheet with formulas & formatting | `office_create` + `office_batch_edit` (formulas are evaluated on write) | openpyxl script ([references/openpyxl-recipes.md](references/openpyxl-recipes.md)) |
| Load thousands of rows | `officecli import book.xlsx /Sheet1 --file data.csv --header` (bash) | pandas `to_excel`, then style with openpyxl |
| Edit an existing model | locate with `office_query`, change with `office_edit` | openpyxl (keep formulas: never save a `data_only=True` workbook) |
| Charts, pivots, validation, conditional formats | `office_edit` add `chart` / `pivottable` / `validation` / CF | openpyxl charts |
| Check formulas | `python scripts/check_formulas.py book.xlsx` | `office_query('cell:contains("#REF!")', mode="selector")` |

OfficeCLI property names and paths for Excel: [references/officecli-xlsx.md](references/officecli-xlsx.md). When unsure, `office_help(file_format="xlsx")` or bash `officecli help xlsx <element>` (e.g. `cell`, `chart`, `pivottable`).

## Requirements for every workbook you deliver

- **Formulas, not pasted results.** Totals, ratios, growth, lookups are formulas (`=SUM(B2:B9)`), so the sheet updates when inputs change. Compute in Python only to *check* the numbers, never to replace the formula. (A static data export or a values-only table the user asked for is the exception.)
- **Zero formula errors.** No `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A` in the delivered file. If you believe an error predates you, prove it by checking the original file's cached value.
- **Inputs are visible and labeled.** Every assumption (rate, price, growth, FX) lives in its own labeled cell and formulas reference it (`=B5*(1+$B$3)`, not `=B5*1.05`). Note the source of hard-coded numbers in an adjacent note or cell comment ("Source: FY2025 10-K, p. 45"; "Provided by user").
- **Follow the request literally.** Exact sheet names, column headers and metric definitions the user specified. Don't redesign what was asked for.
- **Editing someone's workbook: match it.** Its fonts, colors, number formats, layout and formula style override every default here. Write only where the change belongs; never rewrite unrelated formulas or "tidy up" tabs. Save to a new file unless told to edit in place.
- **Readable by default:** header row bold and frozen, sensible column widths, numbers right-aligned with consistent formats, no gridline clutter on presentation sheets, one font family.

## Workflow

1. **Understand the data and the ask.** `office_read` the input (or profile it with pandas). Note sheet names, header rows, units, blanks, merged cells, existing formulas.
2. **Plan the layout** before writing cells: which tab holds inputs, calculations, outputs; where totals go; which cells are inputs. Guidance on structure and formula patterns: [references/workbook-design.md](references/workbook-design.md). Financial models: [references/financial-models.md](references/financial-models.md).
3. **Write 2-3 representative formulas first** and confirm they return what you expect (`office_query` the cell, or `office_read`). A formula can evaluate cleanly and still point at the wrong row.
4. **Build out** the rest in batches (`office_batch_edit`, ~20-60 commands per batch). Copy patterns consistently across rows/periods.
5. **Format**: number formats, widths, freeze panes, header style, input-cell color.
6. **Verify** (next section), fix, re-verify.
7. **Report**: output path, sheets created, key formulas/assumptions, anything unverified.

## Building with the office tools

```json
[{"command":"set","path":"/Sheet1","props":{"name":"Model"}},
 {"command":"set","path":"/Model/A1","props":{"value":"Month","bold":"true"}},
 {"command":"set","path":"/Model/B1","props":{"value":"Revenue","bold":"true"}},
 {"command":"set","path":"/Model/A2","props":{"value":"2026-01-01","numFmt":"mmm-yy"}},
 {"command":"set","path":"/Model/B2","props":{"value":"42000","numFmt":"#,##0"}},
 {"command":"set","path":"/Model/B14","props":{"formula":"SUM(B2:B13)","numFmt":"#,##0","bold":"true"}},
 {"command":"set","path":"/Model/col[A]","props":{"width":"14"}},
 {"command":"set","path":"/Model","props":{"freeze":"A2"}}]
```
- Formulas go in `formula` **without** the leading `=`. Paths: `/Sheet/A1`, ranges `/Sheet/A1:C3`, `/Sheet/row[3]`, `/Sheet/col[B]`. Sheet names are case-sensitive.
- Check exact prop spellings if a command fails (`numFmt` may also be accepted as `numberformat`; `office_help` lists the current names).
- If `set` on an empty cell is rejected, `add` the cell first (`parent=/Sheet1`, `type=cell`, `props.ref=B2`).
- OfficeCLI computes formula results when you write them. If you later change an input that other formulas depend on, re-`set` the dependent formulas (or recalc with LibreOffice) so stored results are not stale.

## Building with openpyxl / pandas

Install on demand: `pip install openpyxl pandas`. Key facts (details and code in [references/openpyxl-recipes.md](references/openpyxl-recipes.md)):
- openpyxl writes formula strings **without results**. Excel recalculates on open, but pandas, `data_only=True` readers, previewers and email clients show blanks until the file is recalculated (`check_formulas.py --recalc --write-back`, needs LibreOffice).
- `load_workbook(data_only=True)` returns cached values and **drops formulas** - never save that workbook over the original. Reading a model needs two loads: one for formulas, one for values.
- `.xlsm`: `load_workbook(path, keep_vba=True)` or the macros are lost.
- Merged cells: write only the top-left cell.
- Cross-sheet references to names with spaces need quotes: `='Input Data'!B5`.
- Functions newer than Excel 2010 must be stored with the `_xlfn.` prefix when written by openpyxl (`_xlfn.TEXTJOIN`, `_xlfn.IFS`, `_xlfn.SWITCH`, `_xlfn.MAXIFS`, `_xlfn.MINIFS`, `_xlfn.CONCAT`), or Excel shows `#NAME?`.
- Avoid dynamic-array functions (`XLOOKUP`, `FILTER`, `SORT`, `UNIQUE`, `SEQUENCE`, `LET`, `LAMBDA`) unless the user is known to use current Excel only: older Excel, LibreOffice and most readers can't evaluate them, and library-written files lack spill metadata. Use `INDEX/MATCH`, `SUMIFS`, `SUMPRODUCT`, and do sorting/filtering/dedup in Python before writing.

## Verify before reporting done

1. **Formula audit:** `python scripts/check_formulas.py out.xlsx` - error cells, formulas without cached values, compatibility problems, external links, row-pattern outliers (one hand-edited cell in a copied row), hardcoded numbers inside formulas. With LibreOffice installed add `--recalc` (checks a recalculated copy) and `--write-back` (stores the results in the file). Exit code 1 means error values exist.
2. **Spot-check values:** `office_read` shows `cell: =FORMULA -> value` for formula cells. Recompute 2-3 key numbers independently (pandas or mental math). Check totals equal the sum of their parts, percentages are stored as fractions (0.15 displays as 15%), and signs are right.
3. **Find errors by query:** `office_query('cell:contains("#DIV/0!")', mode="selector")` (repeat for `#REF!`, `#NAME?`, `#VALUE!`, `#N/A`).
4. **Issues:** bash `officecli view out.xlsx issues` and `officecli validate out.xlsx` when the binary is reachable.
5. **Look at it:** `office_render` (HTML with charts inline) - check for `####` (column too narrow), truncated headers, charts pointing at the right ranges, leftover placeholders (`TBD`, `xxx`, `{{...}}`). For print-like output, `python scripts/render_to_images.py out.xlsx` renders pages to PNG for `read`.

Never ship while the audit reports error cells. "No errors" proves formulas evaluate, not that they are right - the spot-checks in step 2 are what catch wrong ranges.

## Financial model quick rules (unless the file or user says otherwise)

- Font colors: **blue** = hard-coded inputs, **black** = formulas, **green** = links to other sheets, **red** = links to other files; light-yellow fill for key assumptions.
- Formats: currency `$#,##0;($#,##0);"-"` with units in the header ("Revenue ($000)"); percentages `0.0%`; multiples `0.0x`; years as text or `0` format (never `2,026`).
- Negative numbers in parentheses; zeros as `-`.
- Same formula across every projection period; no lone overrides mid-row (if needed, mark them clearly).
- Guard divisions where the denominator can be zero: `=IF(C8=0,"n.a.",B8/C8)` - not a blanket `IFERROR(...,0)`, which hides real breakage.
- Balance checks (assets = liabilities + equity, cash ties out) as visible check rows that read 0.

Full conventions, scenario selectors, sensitivity tables and checks: [references/financial-models.md](references/financial-models.md).

## CSV / data conversion notes

- Preserve leading zeros and long IDs as text (`dtype=str` in pandas; `@` number format).
- Parse dates into real dates (not text) and apply a date format; keep the source's timezone semantics.
- Large data: put raw data on its own sheet as an Excel table, summaries on a separate sheet referencing it with `SUMIFS`/pivot tables.
- Writing CSV back out: confirm delimiter, encoding (UTF-8 with BOM if the user opens it in Excel and it has non-ASCII text), and decimal separator.

<!-- Adapted from Anthropic claude-code skills/xlsx/SKILL.md (Apache-2.0). Modified for CrabAgent. -->

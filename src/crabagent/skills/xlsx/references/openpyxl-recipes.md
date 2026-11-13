# openpyxl and pandas recipes

Install on demand: `pip install openpyxl pandas`. openpyxl writes formulas as text and
never calculates them; see the recalculation note at the end.

## A formatted sheet with live formulas

```python
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

wb = Workbook()
ws = wb.active
ws.title = "Model"

HEADER_FILL = PatternFill("solid", start_color="1F4E79")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF")
INPUT_FONT = Font(name="Arial", color="0000FF")          # blue = hard-coded input
BODY_FONT = Font(name="Arial", color="000000")
THIN = Side(style="thin", color="BFBFBF")

ws["A1"], ws["B1"] = "Growth rate", 0.05                  # labelled assumption cell
ws["B1"].font, ws["B1"].number_format = INPUT_FONT, "0.0%"

headers = ["Year", "Revenue", "Growth"]
for col, text in enumerate(headers, start=1):
    c = ws.cell(row=3, column=col, value=text)
    c.fill, c.font = HEADER_FILL, HEADER_FONT
    c.alignment = Alignment(horizontal="center")

ws["A4"], ws["B4"] = "2025", 1_000_000                    # years stored as text
ws["B4"].font = INPUT_FONT
for r in range(5, 10):
    ws[f"A{r}"] = str(2020 + r)
    ws[f"B{r}"] = f"=B{r-1}*(1+$B$1)"                     # same formula pattern on every row
    ws[f"C{r}"] = f"=IF(B{r-1}=0,\"n.a.\",B{r}/B{r-1}-1)"
ws["A10"], ws["B10"] = "Total", "=SUM(B4:B9)"
ws["A10"].font = ws["B10"].font = Font(name="Arial", bold=True)
ws["B10"].border = Border(top=THIN)

for r in range(4, 11):
    ws[f"B{r}"].number_format = '$#,##0;($#,##0);"-"'
    ws[f"C{r}"].number_format = "0.0%"
    ws[f"C{r}"].alignment = Alignment(horizontal="right")
for col, width in zip("ABC", (14, 16, 10)):
    ws.column_dimensions[col].width = width
ws.freeze_panes = "A4"
ws.sheet_view.showGridLines = False
wb.save("model.xlsx")
```

Column width from content: `max(len(str(c.value or "")) for c in column) + 2`, capped
around 50; formulas need a manual width estimate.

## Reading a workbook correctly

```python
from openpyxl import load_workbook
wb_f = load_workbook("in.xlsx")                    # formulas as strings ("=SUM(B2:B9)")
wb_v = load_workbook("in.xlsx", data_only=True)    # last cached values (None if never calculated)
for ws in wb_f.worksheets:
    print(ws.title, ws.dimensions, ws.max_row, ws.max_column)
```
- Never save `wb_v`: it has no formulas, so saving replaces them with constants.
- `read_only=True` for very large files (streaming; no random access, no saving).
- Merged ranges: `ws.merged_cells.ranges`; only the top-left cell holds a value.
- Hidden rows/cols and sheets: `ws.row_dimensions[r].hidden`, `ws.sheet_state`.

pandas: `pd.read_excel(path, sheet_name=None)` returns a dict of all sheets;
`header=` picks the header row; `dtype={"id": str}` keeps leading zeros. pandas reads cached
values only - a freshly written openpyxl workbook reads as NaN in formula columns.

## Bulk data with pandas, styling with openpyxl

```python
import pandas as pd
with pd.ExcelWriter("report.xlsx", engine="openpyxl") as xw:
    df.to_excel(xw, sheet_name="Data", index=False)
    summary.to_excel(xw, sheet_name="Summary", index=False)
    ws = xw.sheets["Data"]
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
```
Then add formulas on the Summary sheet that reference Data (`=SUMIFS(Data!$D:$D,Data!$A:$A,$A2)`)
instead of writing pandas-computed totals, unless the user wants a static extract.

Excel table (structured, filterable range):
```python
from openpyxl.worksheet.table import Table, TableStyleInfo
tab = Table(displayName="Sales", ref=f"A1:{get_column_letter(ws.max_column)}{ws.max_row}")
tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
ws.add_table(tab)
```

## Conditional formatting and validation

```python
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule
from openpyxl.worksheet.datavalidation import DataValidation

ws.conditional_formatting.add("C5:C9", CellIsRule(operator="lessThan", formula=["0"],
                              font=Font(color="C00000")))
ws.conditional_formatting.add("D2:D200", ColorScaleRule(start_type="min", start_color="FFFFFF",
                              end_type="max", end_color="1F77B4"))
ws.conditional_formatting.add("E2:E200", DataBarRule(start_type="min", end_type="max", color="5B9BD5"))

dv = DataValidation(type="list", formula1='"Open,In progress,Done"', allow_blank=True)
ws.add_data_validation(dv)
dv.add("F2:F500")
```

## Charts

```python
from openpyxl.chart import BarChart, LineChart, Reference

chart = BarChart()
chart.type = "col"
chart.title, chart.y_axis.title, chart.x_axis.title = "Revenue by year", "Revenue ($)", None
data = Reference(ws, min_col=2, min_row=3, max_row=9)         # include header -> series name
cats = Reference(ws, min_col=1, min_row=4, max_row=9)
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
chart.legend = None                                           # single series: no legend
chart.width, chart.height = 18, 9                             # cm
ws.add_chart(chart, "E3")
```
Charts reference cell ranges, so they stay live. Keep one value axis per chart (no dual
axes); use two charts instead. For chart-type choice and color, see the
data-visualization skill if it is available.

## Comments, names, protection, print setup

```python
from openpyxl.comments import Comment
from openpyxl.workbook.defined_name import DefinedName
ws["B1"].comment = Comment("Source: management plan, Oct 2026", "Analyst")
wb.defined_names["GrowthRate"] = DefinedName("GrowthRate", attr_text="Model!$B$1")
from openpyxl.styles import Protection
ws.protection.sheet = True; ws["B1"].protection = Protection(locked=False)   # inputs stay editable
ws.page_setup.orientation = "landscape"; ws.page_setup.fitToWidth = 1; ws.sheet_properties.pageSetUpPr.fitToPage = True
ws.print_title_rows = "3:3"
```

## Recalculation

Files written by openpyxl contain formulas without results. Options:
1. Leave it - Excel recalculates on open. Fine when the user opens it in Excel and nothing
   else needs the values.
2. `python scripts/check_formulas.py model.xlsx --recalc --write-back` - LibreOffice computes
   every formula and the results are stored; the audit then checks real values.
   LibreOffice may drop features it does not support (slicers, some chart styling, VBA in
   .xlsm) - keep the original if that matters.
3. Build or re-touch the formulas with `office_edit` / `office_batch_edit`, whose engine stores
   computed results.

Install LibreOffice if needed: `apt install libreoffice-calc` / `brew install --cask libreoffice`
/ `winget install TheDocumentFoundation.LibreOffice`.

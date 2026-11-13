# OfficeCLI cheat sheet - Excel (.xlsx)

Starting point only; the installed CLI is authoritative. Look up exact names with
`office_help(file_format="xlsx")` or bash `officecli help xlsx <element>` (`cell`, `sheet`,
`row`, `col`, `chart`, `pivottable`, `validation`, `conditionalformatting`, `table`).

## Tool mapping

- `office_read(file, sheet=, cols="A:F", offset=, max_lines=)` - values per sheet, then a
  "Formula cells" section: `/Sheet/B14: =SUM(B2:B13) -> 135000` (first 50 formulas).
- `office_query("/Model/B14")` - one cell as JSON (formula, cachedValue, format).
- `office_query('cell:has(formula)', mode="selector")` - every formula cell.
- `office_edit` / `office_batch_edit` - `set`, `add`, `remove`, `move`, `swap`.
- `office_render` - HTML rendering of all sheets with charts.

Bash-only: `officecli import book.xlsx /Sheet1 --file data.csv --header` (also
`--format tsv`, `--start-cell B2`), `officecli view book.xlsx issues`,
`officecli validate book.xlsx`, `officecli dump book.xlsx /Sheet1 -o sheet.json`,
`officecli close book.xlsx` (flush before pandas/openpyxl read the file).

## Paths

```
/Sheet1               sheet (set name, freeze, tabColor, orientation, fitToPage, sort)
/Sheet1/A1            cell              /Sheet1/A1:D20   range (merge, bulk format)
/Sheet1/row[3]        row (height)      /Sheet1/col[B]   column (width)
/Sheet1/chart[1]      chart             /Sheet1/chart[1]/axis[@role=value]
```
Sheet names are case-sensitive. A brand-new sheet may have no cell nodes; if `set`
fails on an empty cell, `add` it (`parent=/Sheet1`, `type=cell`, `props.ref=B2`).
Add a sheet: `add` to `/` type `sheet` `{"name":"Inputs"}`.

## Cell properties

`value` (number, text, ISO date), `formula` (no leading `=`), `numFmt` (also
`numberformat`), `bold`, `italic`, `underline`, `font`, `size`, `color` (font), `fill`
(background), `alignment.horizontal` (left/center/right), `alignment.vertical`,
`wrapText`, `border.all` / `border.bottom` (`single;1pt;BFBFBF`), `merge` (on a range path:
`true`, or `merge=A1:C1` on a cell), `locked`, `link` (hyperlink), comments via `add`
type `comment`.

Number format strings: `#,##0`, `#,##0.00`, `$#,##0;($#,##0);"-"`, `0.0%`, `0.0x`,
`yyyy-mm-dd`, `mmm-yy`, `@` (text).

Sheet: `name`, `freeze` (`A2` freezes row 1; `B2` row 1 + column A), `tabColor`,
`orientation`, `fitToPage` (`true` or `1x0` = one page wide), `sort` (`"C desc"`, with
`sortHeader=true`), `autofilter`.

Row/col: `height`, `width` (characters), `hidden`. Inserting rows/cols with `add` rewrites
formula references like Excel does (`index` for rows/cols is 1-based).

## Charts

`add` to `/Sheet1` type `chart`:
```json
{"chartType":"column","title":"Revenue by month","dataRange":"Model!A1:C13",
 "catTitle":"Month","axisTitle":"Revenue ($)","legend":"bottom","anchor":"E2:N20"}
```
- `chartType`: column, columnStacked, columnPercentStacked, bar, barStacked, line, area,
  pie, doughnut, scatter, bubble, radar, combo, waterfall, histogram, boxWhisker, funnel,
  treemap, sunburst, pareto (check help for the installed list).
- Data either from `dataRange` (first column = categories, first row = series names) or
  inline: `categories="Jan,Feb,Mar"` + `series1="North:10,12,14"` + `series2=...`, or
  `data="East:198;West:180"` for one series.
- Styling: `colors` (comma-separated hex in series order), `dataLabels`, `labelPos`,
  `gapwidth`, `plotFill`, `title.size`, `title.bold`.
- Move/resize later: `set /Sheet1/chart[1] {"anchor":"E2:N20"}`. Series are fixed after
  creation - to change series, remove and re-add the chart.
- Axis bounds: `set /Sheet1/chart[1]/axis[@role=value] {"min":"0","max":"100000"}`.

## Pivot tables, validation, conditional formatting

```json
{"command":"add","parent":"/Summary","type":"pivottable",
 "props":{"source":"Data!A1:E5000","rows":"Region,Category","cols":"Quarter",
          "values":"Revenue:sum,Units:sum","position":"A3"}}
```
Aggregations: sum, count, average, max, min, stdDev, var, countNums; `showDataAs`
(percent_of_total, percent_of_row, percent_of_col, running_total); `layout`
(compact/outline/tabular); `grandTotals`, `subtotals`, `sort`.

Data validation: `add` type `validation` with `sqref` (range), `type` (list, whole,
decimal, date, textLength), `formula1` / list values, `showError`, `error`, `errorTitle`.

Conditional formatting: `add` CF types such as `databar`, `colorscale`, `iconset`,
`cellIs` (operator + value + fill), `formulacf`, `topN`, `aboveAverage` - see
`officecli help xlsx` for the element name and props in the installed version.

## Queries useful for QA

```
cell:has(formula)            every formula
cell:contains("#REF!")       broken references (also #DIV/0!, #VALUE!, #NAME?, #N/A)
cell[type=Number]            typed filter
Sheet1!B[value!=0]           sheet-scoped column filter
Data!row[Amount>5000]        rows by column header value
cell:contains("{{")          leftover template tokens
merge                        all merged ranges
```

## Values

Colors as hex without `#` (`1F4E79`). Booleans as `"true"`/`"false"`. Text that starts
with `=` but must stay text: prefix with an apostrophe.

# Financial model conventions

Apply when building budgets, forecasts, three-statement models, valuations (DCF, comps,
LBO), unit economics or scenario analyses - unless the user or the existing file uses
different conventions, in which case match theirs.

## Color and format code

| Cell type | Font | Fill |
|---|---|---|
| Hard-coded input / historical actual | blue `0000FF` | none |
| Key assumption the user should review | blue | light yellow `FFF2CC` |
| Formula on the same sheet | black `000000` | none |
| Link to another sheet | green `008000` | none |
| Link to another file | red `FF0000` | none |
| Check cell | black; red + light-red fill when failing (conditional format) | |

Presentation / summary sheets may show all results in black - the color code is for the
working areas.

| Quantity | Format | Note |
|---|---|---|
| Currency | `#,##0;(#,##0);"-"` or `$#,##0;($#,##0);"-"` | scale in header: "($000)", "($m)" |
| Per-share | `$0.00` | |
| Percent | `0.0%` | store 0.153, not 15.3 |
| Multiples | `0.0"x"` | |
| Counts / FTE | `#,##0` / `0.0` | keep fractional FTE |
| Years | text `"2026"` or number format `0` | never `2,026` |
| Periods | `mmm-yy` on a real date; quarters as labels `Q3 FY26` derived from dates | |

Don't double-scale (values in thousands AND a /1000 in formulas), and keep one scale per
table.

## Structure

1. **Assumptions** block/sheet: drivers by period (growth, price, volume, margins,
   headcount, capex, DSO/DPO, tax rate, WACC), each labeled with unit and source.
2. **Scenario selector** (if scenarios are requested): one cell with the active case
   number and data validation (1=Base, 2=Upside, 3=Downside). Each driver has its case
   rows plus one "active" row: `=CHOOSE($C$3, C12, C13, C14)`. The model links only to the
   active rows - never maintain parallel copies of the whole model per case.
3. **Build / schedules**: revenue build (volume x price, customers roll-forward), cost
   build, headcount, capex and depreciation, working capital, debt schedule (opening +
   draws - repayments = closing; interest on opening or average balance, documented).
4. **Statements**: income statement, balance sheet, cash flow - linked, not re-typed.
5. **Outputs**: summary KPIs, valuation, charts.
6. **Checks**: balance sheet balances, cash flow ending cash = balance sheet cash, debt
   schedule ties, sum of segments = total. Show differences to 2 decimals; flag non-zero.

Historical columns hold actuals (blue, sourced); forecast columns hold formulas. Mark the
boundary (header label "A" / "F" or a subtle border), and keep the formula identical
across all forecast columns.

## Common formulas

```
Growth projection      =C10*(1+D$5)                 driver row 5, same formula every column
Margin                 =IF(D10=0,"n.a.",D20/D10)
CAGR                   =(H10/C10)^(1/(COLUMNS(C10:H10)-1))-1
Roll-forward close     =D30+D31-D32                  opening + additions - reductions
Next opening           =D33                          (in E30)
Depreciation (SL)      =MIN(cost/life, remaining_book)
NPV (end-of-period)    =NPV(WACC, D40:H40)
NPV (mid-year)         =SUMPRODUCT(D40:H40, 1/(1+WACC)^(D$2:H$2-0.5))
IRR / XIRR             =IRR(C50:H50)  /  =XIRR(C50:H50, C2:H2)   needs a negative first flow
Terminal value (GGM)   =H40*(1+g)/(WACC-g)           check WACC > g
Payback                cumulative cash row + MATCH(TRUE, INDEX(cum>=0,0), 0)
```

Guard: `WACC > g`, IRR needs both signs, avoid dividing by projected zero revenue.

## Sensitivity tables

For "show valuation vs. WACC and growth": build a 2-D grid whose corner cell links to the
output and whose row/column headers are the test values, then use Excel's Data Table
(What-If) feature when it can be created in the tool you're using; otherwise compute each
cell with the closed-form formula that references the header values (e.g. a DCF terminal
value formula), not with pasted numbers. Highlight the base case. Verify the base-case cell
equals the model's output.

## Sourcing and documentation

- Each historical figure: source note (document, page, period) in a comment or a source
  column.
- Each assumption: rationale and source ("Mgmt guidance, Q3 call", "Industry avg., 2025").
- Don't round stored values to make them look nice - format the display instead.
- State the accounting basis (cash vs. accrual), currency, fiscal year end and units once,
  near the top.

## Review checklist

- Inputs are only in input cells; no numbers typed inside formulas (except 0/1/12-type
  constants) - `check_formulas.py` lists literals.
- Formulas consistent across each row - `check_formulas.py` flags row outliers.
- All checks read 0; no error values; no circular references unless documented.
- Signs: costs negative or positive consistently, and the statements use them correctly.
- Change one key input and confirm outputs move in the expected direction.

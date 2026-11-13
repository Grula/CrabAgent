# Workbook structure and formula patterns

## Size the workbook to the job

A focused request (a tracker, a one-off analysis, a calculator) is usually one sheet:
inputs in a labeled block, the calculation beneath or beside them, the answer at the top.
Add sheets only when they serve a different reader or a different update cadence.

Signs of over-building: a "Cover" and "Readme" for a 20-row list; Jan/Feb/Mar tabs with
identical layouts (use one table with a Month column); a Summary tab that only re-links
cells without adding totals or comparisons; three cells of assumptions on their own tab.

When several sheets are justified, a common left-to-right order is:
**Summary / outputs -> Assumptions -> Calculations -> Data / sources -> Checks.**
Readers land on the answer; the evidence is one tab away.

## Roles of cells

- **Raw data** - pasted or imported records, kept intact. Clean or map them in separate
  labeled columns or a separate sheet; never overwrite the source values.
- **Inputs / assumptions** - every number a person might change, in one place, labeled
  with unit and source. Visually marked (blue font, light-yellow fill).
- **Calculations** - formulas only, flowing from inputs and data. Show meaningful steps
  (volume x price = revenue) instead of one opaque mega-formula, but don't add trivial
  helper rows.
- **Outputs** - totals, KPIs, charts; formulas that point at calculations.
- **Checks** - formulas that compare two independent paths to the same number (should
  be 0). Nothing else in the model may depend on a check cell.

Each number is calculated in exactly one place; everywhere else links to it.

## Formula construction

- Prefer `SUMIFS` / `COUNTIFS` / `AVERAGEIFS` (they extend to more criteria) over the
  single-criterion versions for new work.
- Lookups: `INDEX(return_range, MATCH(key, key_range, 0))` works everywhere; `VLOOKUP`
  with exact match (`FALSE`) is fine for simple cases. Decide what should happen on a
  missing key and duplicates (first match vs. sum) - don't let a lookup silently pick one.
- Long `IF` chains for categories: replace with a small mapping table + lookup.
- Weighted totals: `SUMPRODUCT(qty_range, price_range)`.
- Ratios of totals: compute from summed numerator / summed denominator, never by
  averaging row-level percentages.
- Avoid volatile or fragile constructs unless needed: `INDIRECT`, `OFFSET`, whole-column
  references inside array maths, hard-coded row numbers in text.

### Anchoring ($)

Fix only what must stay fixed when the formula is filled:

| Reference | Fixed | Typical use |
|---|---|---|
| `C8` | nothing | the value next to the formula |
| `C$4` | row 4 | period header shared by a whole block, filled across |
| `$A8` | column A | row label / item key, filled down |
| `$B$3` | the cell | one global assumption used everywhere |

Example: `=SUMIFS(Data!$D:$D, Data!$B:$B, C$4, Data!$A:$A, $A8)` fills across periods and
down items without edits.

### Dates and periods

- Store real dates and format them (`mmm-yy`, `yyyy-mm-dd`); don't store "Jan-26" as text.
- Monthly sum from daily rows: `=SUMIFS(Amt, Date, ">="&C$4, Date, "<"&EDATE(C$4,1))`
  (C4 = first day of the month; the exclusive upper bound handles timestamps).
- Use a fixed reporting date cell instead of `TODAY()` when the numbers should not drift.
- Periods run left to right, oldest first; annual summary columns to the left of monthly
  detail when both are shown.
- Roll stocks (balances, headcount) by ending value; sum flows (revenue, cost).

### Missing data and errors

- Distinguish a real 0 from "no data" and from "not applicable". Use `"n.a."` or a blank
  deliberately, and right-align such text among numbers.
- Guard the specific expected problem: `=IF(C8=0,"n.a.",B8/C8)`; `IFNA(lookup,"not found")`
  for a missing key. Don't wrap everything in `IFERROR(...,0)` - it turns broken references
  into believable zeros.
- A `SUMIFS` returning 0 doesn't prove matching rows exist; add a `COUNTIFS` check where
  absence matters.
- Never invent missing source numbers. Leave the cell visibly empty/flagged and say what
  is missing.

### Circular references

Avoid them. If a model genuinely needs one (interest on average debt balance), isolate it,
document it next to the cells, add a circuit-breaker switch cell, and tell the user that
iterative calculation must be enabled. Never enable iteration silently.

## Layout and presentation

- Title in A1 (bold, larger), a one-line description/units row, then a blank row, then
  the table.
- Header row: bold, fill color, bottom border, wrap text, frozen panes below it.
- Numbers right-aligned, text left-aligned; consistent decimals per column; thousands
  separators; units in the header, not repeated in every cell.
- Column widths fit the content (no `####`); avoid widths over ~50 characters - wrap instead.
- Totals row: bold, top border; totals sum detail rows only (never include subtotals twice).
- Use at most one accent color plus neutrals; reserve red/green for meaning (negative,
  pass/fail) and pair it with a sign or label.
- Tables longer than a screen: freeze the header, add an autofilter, consider an Excel
  table object.
- Input forms or templates you create for others: a short "how to use" note naming the
  editable cells, data validation dropdowns for constrained fields, and one example row.

## Editing an existing workbook

1. Read every sheet's layout and formulas before changing anything; note the input-cell
   styling convention.
2. Change only what was asked plus what depends on it (extend ranges, totals, charts and
   named ranges when you add rows/columns).
3. Inserting rows inside a range keeps `SUM` ranges correct; appending below a total does
   not - extend the formulas.
4. Re-run the formula audit and compare key outputs before/after; explain any changed
   totals.

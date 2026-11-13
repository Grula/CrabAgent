# Data-quality checks

Run the checks that could change the answer to *this* question; report each real issue
with evidence (counts, rates, affected periods/segments), why it matters, likely cause,
and what you did about it.

## Check catalogue

| Dimension | What to check | Quick pandas |
|---|---|---|
| Grain / uniqueness | expected key unique? exact duplicate rows? near-duplicates (case, whitespace)? | `df.duplicated(subset=key).sum()`, `df[key].str.strip().str.lower().duplicated()` |
| Completeness | null rate per column, per period, per source; required fields populated; sentinel values | `df.isna().mean()`, `df.groupby(period)[col].apply(lambda s: s.isna().mean())` |
| Validity | types parse; ranges (no negative qty, % in 0-1, dates plausible); allowed values for enums; ID/email/country formats | `pd.to_numeric(s, errors="coerce").isna()`, `~s.isin(allowed)` |
| Consistency | cross-field rules (`cancelled_at` set iff status cancelled); same unit/currency; totals agree with another source | boolean masks, `groupby().sum()` vs. reference |
| Integrity | foreign keys match parents; joins don't fan out | `merge(..., how="left", indicator=True, validate="m:1")` |
| Timeliness | max date vs. expected; last period complete; late arrivals; backfills that rewrote history | `df[date].max()`, rows per day for the last 2-3 weeks |
| Volume & shape | row count per period vs. history; new or vanished categories; distribution shift | `value_counts()` per period, quantiles per period |

## Snippets

```python
import pandas as pd

df = pd.read_csv("orders.csv", dtype={"order_id": str, "zip": str}, parse_dates=["created_at"])

# Grain: is order_id unique? which rows collide?
dups = df[df.duplicated("order_id", keep=False)].sort_values("order_id")

# Row counts per day + incomplete last day
daily = df.set_index("created_at").resample("D").size()
print(daily.tail(14))

# Null rate by week for the columns that matter
weekly_nulls = (df.set_index("created_at")[["amount", "country"]]
                  .isna().resample("W").mean().round(3))

# Join fan-out guard: raises if customers has duplicate keys
merged = df.merge(customers, on="customer_id", how="left", validate="m:1", indicator=True)
print(merged["_merge"].value_counts())          # left_only = orders without a customer

# Robust outliers (median absolute deviation)
s = df["amount"]
mad = (s - s.median()).abs().median()
outliers = df[(s - s.median()).abs() > 6 * 1.4826 * mad]

# Category drift between two periods
share = (df.assign(p=df["created_at"].dt.to_period("M"))
           .groupby(["p", "channel"]).size().unstack(fill_value=0))
share = share.div(share.sum(axis=1), axis=0).round(3)
```

## Cleaning rules

- Never overwrite the raw file; clean into new columns/frames and keep a log of decisions.
- Normalize before deduplicating (trim, lowercase emails, unify country codes).
- Don't drop rows silently - count what you drop and why, and check the drop doesn't bias
  a segment (e.g. nulls concentrated in one country).
- Keep "unknown" distinct from zero and from "not applicable".
- Convert units/currencies explicitly with a stated rate and date.
- Exclude test, internal and fraud accounts only with a rule you can state.
- Time zones: convert timestamps to the reporting time zone before bucketing by day.

## Severity

- **Critical** - invalidates the answer: wrong grain/duplicated rows in the measured
  table, missing periods in the comparison window, broken join dropping a segment.
- **High** - materially biases the result: large null spike in a key field, definition
  changed mid-window, test data mixed in.
- **Medium** - localized; document and work around.
- **Low** - cosmetic or known edge cases.

Fix critical/high issues (or scope the analysis to avoid them) before drawing conclusions;
list medium/low ones in the caveats.

## Reporting a data-quality review

When the task *is* the quality review, structure it as: dataset and grain summary ->
checks run -> findings (what failed, evidence, impact, severity, likely cause) ->
recommended fixes and automated tests worth adding (key uniqueness, not-null on required
columns, accepted values, referential integrity, freshness thresholds) -> open questions.
Prefer small tables of rates over raw dumps.

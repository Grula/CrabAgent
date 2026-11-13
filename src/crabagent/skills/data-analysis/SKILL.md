---
name: data-analysis
description: End-to-end analysis of tabular data (CSV, Excel, JSON, Parquet) - profile and validate data quality, define metrics/KPIs, analyze trends, segments and drivers, sanity-check, report findings with caveats. Use to analyze a dataset, explain a metric change, design KPIs, or check data quality.
---

# Data Analysis

Good analysis answers a specific question with numbers you have checked, and says how
sure you are. Follow the stages in order; skip only what the question makes unnecessary.
Scripts and references are relative to the skill directory shown when this skill loads.

**Tools:** `bash` with Python (`pip install pandas` if missing; add `openpyxl` for Excel,
`pyarrow` for Parquet, `duckdb` for SQL over files), `office_read` for a quick look at
.xlsx, the `pdf` skill for tables inside PDFs, `web_search` only for external context
(benchmarks, events) - and cite it.

## 1. Frame the question

Write down before touching data:
- **The decision or question** ("Should we expand the trial to EMEA?", "Why did signups
  drop last week?"). If the request is vague ("analyze this"), state the 2-3 questions you
  will answer and proceed; ask only if a wrong guess would waste the work.
- **The unit of analysis** (order, customer, session, day) and **population** (who/what is
  included, time window, filters).
- **The metric definitions** - numerator, denominator, time window, exclusions. Use the
  user's/organization's definitions when they exist; don't silently invent a variant.
- **The comparison** that makes a number meaningful: previous period, same period last
  year, target, control group, peer segment.

## 2. Load and profile

```bash
python scripts/profile_data.py data.csv --key order_id --date created_at --freq W
```
Reports shape, types, missingness, sentinel values (`n/a`, `-1`, `9999`), whitespace/case
variants, numbers or dates stored as text, duplicate rows, candidate keys, duplicate keys,
date coverage, empty periods, a suspiciously small last period, future dates.
Then look at a few raw rows yourself (`df.sample(10)`), including the extremes.

## 3. Validate data quality - before any conclusion

Decide which issues matter *for this question* and resolve or document each:
- **Grain:** one row per what? Duplicated keys or join fan-out inflate sums and counts.
- **Completeness:** missing periods, partially loaded last day/week, late-arriving data,
  null spikes after a release or migration.
- **Validity:** impossible values (negative quantities, dates in the future, percentages
  > 100), unit/currency mixes, test/internal accounts.
- **Consistency:** totals that should match another source; status vs. timestamp
  contradictions; category labels that changed meaning.
Details, pandas snippets and severity guidance: [references/data-quality.md](references/data-quality.md).

Clean in code (never by hand-editing the file), keep the raw data untouched, and keep a
list of every cleaning decision - it goes in the caveats.

## 4. Analyze

- **Start with the overall number and its trend**, at a grain that shows the pattern
  without noise (daily for weeks of data, weekly/monthly for longer spans).
- **Then cut by the dimensions that plausibly explain it** (segment, region, channel,
  product, cohort) - chosen from how the business works, not every column available.
- **Compare like with like:** same period length, same definitions, complete periods only,
  seasonality accounted for (YoY or same weekday).
- **Use rates for performance, counts for size.** A segment can be the largest
  contributor and still perform fine.
- **Decompose changes** into volume vs. rate vs. mix before naming causes:
  [references/diagnosing-changes.md](references/diagnosing-changes.md).
- **Distributions over averages** when values are skewed (revenue per customer, latency):
  report median and percentiles; check whether a few outliers drive the mean.
- **Statistics proportional to the claim:** confidence intervals or a simple test for
  differences that matter; mention sample size; beware of many comparisons (some will look
  "significant" by chance).
- Metric design and KPI recommendations (outcome / driver / guardrail metrics, targets):
  [references/metrics-and-kpis.md](references/metrics-and-kpis.md).

Keep the analysis reproducible: one script or notebook from raw file to final numbers,
saved next to the outputs when the user wants deliverables.

## 5. Sanity-check the results

Before writing anything:
- **Recompute key numbers a second way** (pandas vs. SQL/duckdb, or by hand for a
  segment). Totals of segments should equal the overall total when segments are exclusive.
- **Reasonableness:** order of magnitude vs. known totals; no rates outside 0-100%; no
  impossible jumps; flat lines or exact round numbers investigated.
- **Boundary cases:** first/last period complete? New categories appearing mid-period?
  Time zones shifting days?
- **Join checks:** row counts before/after every merge; unmatched keys counted.
- **Ratio math:** aggregate ratios = sum(numerator)/sum(denominator), never the average
  of row-level ratios (unless that is the definition).
- **Alternative explanations:** for every "X caused Y", list what else could explain it
  (seasonality, mix shift, a tracking change, a price change, a one-off event) and what
  evidence would distinguish them. Correlation and accounting decomposition are not
  causation - label untested mechanisms as hypotheses.

## 6. Report findings

Lead with the answer, then the evidence, then caveats and next steps. Format and wording
guidance: [references/communicating-results.md](references/communicating-results.md). Minimum content:
- **Answer** in 1-3 sentences with the key numbers and their comparison
  ("Signups fell 18% WoW (4,120 -> 3,380), almost entirely from paid search in the US").
- **Evidence:** a few tables/charts (use the data-visualization skill), each with a
  one-line takeaway.
- **Confidence and caveats** that would change the reader's interpretation - data gaps,
  definitions used, assumptions, what was not checked. State each once, next to the claim
  it affects.
- **Recommendations / next steps** that follow from the evidence, including what data
  would resolve open questions.
- **Method notes:** source files, filters, definitions, and where the script lives.

Deliverable formats: a chat answer for quick questions; a Markdown/Word report (docx
skill), workbook with the supporting tables (xlsx skill), slides (pptx skill) or an HTML
dashboard (data-visualization skill) when asked. Charts and numbers in every format must
match the checked analysis exactly.

## Red flags to stop and re-check

- A result that is surprising, very large, or exactly what someone hoped for.
- Totals that don't match the source system or a previous report.
- A metric moving because its denominator changed.
- A segment trend that reverses when aggregated (Simpson's paradox).
- The latest period always looking worse (incomplete data) or better (backfill).

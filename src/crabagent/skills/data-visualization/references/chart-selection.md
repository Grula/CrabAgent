# Choosing the chart form

Decide what the reader must *do* with the data; the task picks the form.

## Maybe it isn't a chart

| The data is | Show | Not |
|---|---|---|
| One current value (and its change) | A big-number tile: value, delta vs. comparison, optional sparkline | A one-bar chart |
| A handful of headline numbers | A row of 3-5 tiles | A grouped bar chart |
| Exact values people will look up | A table (sorted, formatted, maybe with inline bars) | A chart with a label on every mark |
| A single ratio vs. a limit (quota used, % complete) | A progress bar / meter | A two-slice pie |
| Two numbers | A sentence ("Revenue rose from $3.1M to $3.9M") | Any chart |

## Task -> form

| Reader's task | Default | Alternatives / notes |
|---|---|---|
| Compare amounts across categories | Bar chart, sorted, one color | Dot plot / lollipop when values are close and far from zero (zero baseline less critical for dots); horizontal bars for long labels |
| Rank / top N | Horizontal sorted bars, top 5-15 | Table with inline bars for long lists |
| Change over time (1-4 series) | Line chart | Column chart for few discrete periods (<= ~8) or when periods are totals (monthly sales) |
| Change over time (many series) | Small multiples of lines with shared axes | Highlight one line, gray the rest |
| Before vs. after per item | Dumbbell / slope chart | Grouped bars for 2-4 items |
| Part of a whole (one total) | Single stacked bar or sorted bars of shares | Pie/donut only for 2-5 clearly different parts; treemap for many parts in a hierarchy |
| Composition over time | Stacked area / stacked columns (<= 5 parts) | 100% stacked when only shares matter; lines when part trends matter more than total |
| Deviation from a target/baseline | Diverging bars around zero, or line + reference line | Bullet chart for actual vs. target |
| Distribution of one variable | Histogram (sensible bin width) | Density curve; box plot when comparing groups; strip/beeswarm for small n |
| Distribution across groups | Box plots or violin, sorted by median | Small-multiple histograms |
| Relationship between two numbers | Scatter (label notable points; trend line if meaningful) | Hexbin/2-D density for thousands of points; bubble for a third measure (use sparingly) |
| Matrix / two categorical dimensions | Heatmap with a sequential scale (diverging if centered) | Cohort retention triangles are heatmaps |
| Flow from start to end value | Waterfall (bridge) | Only when components add up exactly |
| Funnel / stage progression | Horizontal bars per stage with conversion % between | Funnel shape distorts comparison - avoid |
| Geography | Choropleth (rates, not totals) or proportional symbols (totals) | Bars by region are often clearer |
| Schedule / timeline | Gantt-style horizontal bars on a time axis | |
| Uncertain estimate / forecast | Point + interval, or line + shaded band | Name the interval type |

## Rules of thumb

- **Time goes left to right** on the x-axis; categories without order are sorted by value.
- **Few periods (2-3)**: don't imply a trend line; use bars, a slope chart, or tiles.
- **Averages can hide** spread and mix shifts - show the distribution or segments when the
  conclusion depends on them (Simpson's paradox: aggregate and segment trends can disagree).
- **Rates vs. counts:** compare performance with rates (comparable denominators and
  windows); size opportunities with counts. Often show both (rate on the chart, n in the
  label or tooltip).
- **Stacking hides** every segment except the bottom one - if readers must compare a
  middle segment over time, unstack it (small multiples or lines).
- **Log scale** only for data spanning orders of magnitude or for growth rates, and label
  it as log.
- **Grouping into "Other"** only for mutually exclusive, additive categories. Never sum
  percentages, averages or overlapping audiences.
- **Same question, same form:** repeated chart types across a report are fine; don't vary
  forms for novelty.

## Common failure catalogue

| Problem | Fix |
|---|---|
| Dual y-axes | Two stacked charts with aligned x, or index to 100 |
| Spaghetti (8+ lines) | Small multiples, or highlight 1-2 and gray the rest |
| Truncated bar axis | Start at zero; if differences are tiny, chart the difference itself |
| Legend far from data | Direct labels at line ends / on bars |
| Every point labeled | Label extremes, the latest point, and the series that matters |
| Rotated, overlapping x labels | Horizontal bars, abbreviations, fewer ticks, or wrapping |
| Pie with 9 slices | Sorted bar chart |
| Value-ramp colors on unordered categories | One color for all bars; reserve color for highlighting |
| Rainbow heatmap | Single-hue sequential (or diverging around a meaningful midpoint) |
| Red/green good/bad only | Blue/orange, plus +/- signs or icons |
| Area/bubble sized by radius | Size by area (matplotlib `s=` is area; plotly `sizemode="area"`) |
| Cumulative chart presented as growth | Show the per-period values |
| Missing periods drawn as zero | Break the line or mark the gap |

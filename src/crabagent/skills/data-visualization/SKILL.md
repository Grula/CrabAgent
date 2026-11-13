---
name: data-visualization
description: Design clear, honest, accessible charts and dashboards - chart choice, colorblind-safe color, labeling - output as matplotlib/plotly images, single-file HTML dashboards, or native Office charts. Use before writing any chart, plot, KPI tile or dashboard, or to make a visualization clearer.
---

# Data Visualization

A chart exists to answer one question for one reader. Work through the steps in order -
most bad charts come from picking colors and chart types before deciding what the
reader must see. Scripts and references are relative to the skill directory shown when
this skill loads.

## Procedure

1. **State the question and the takeaway** in one sentence ("Which regions missed plan
   in Q3, and by how much?"). If you can't, you are not ready to chart.
2. **Check the data** before drawing it: units, time grain, missing periods, duplicates,
   outliers, whether values are additive. A chart of wrong numbers is worse than no chart
   (use the data-analysis skill for real checks).
3. **Pick the form** from what the reader must do - compare, see change, see
   composition, see distribution, see relationship. Sometimes the answer is a single big
   number or a table, not a chart. Details: [references/chart-selection.md](references/chart-selection.md).
4. **Assign color by job, last:** one neutral + one accent for most charts; a categorical
   palette only when the series themselves are the subject; sequential (one hue light to
   dark) for magnitude; diverging (two hues + neutral middle) for above/below a reference.
   Validate any categorical palette:
   `python scripts/check_palette.py "#1F5FAD,#2A9D5C,#6B4FBB" --background "#FFFFFF"`.
   Palettes and rules: [references/color-and-accessibility.md](references/color-and-accessibility.md).
5. **Label for the reader:** a title that states the finding, axis titles with units,
   direct labels on lines/bars where they fit, source and date in a small note. Remove
   chart junk (heavy gridlines, borders, 3D, shadows, redundant legends).
6. **Build** in the medium that fits the destination (table below).
7. **Render and look at it** - open the PNG with `read`, or screenshot the HTML. Check
   label collisions, clipped text, legend overlap, unreadable ticks, wrong scale, and
   that the takeaway is visible in 5 seconds. Fix and re-render.

## Choose the output medium

| Destination | Build with | Reference |
|---|---|---|
| Image for chat, a report, a PDF or a slide | matplotlib (PNG 200 dpi / SVG) | [references/matplotlib-plotly.md](references/matplotlib-plotly.md) |
| Interactive single chart | plotly -> standalone HTML | same |
| Dashboard / multi-chart page | one self-contained HTML file (inline SVG or a pinned chart library) | [references/html-dashboards.md](references/html-dashboards.md) |
| Inside an Excel workbook | native chart via `office_edit` (xlsx skill) - stays linked to cells | xlsx skill |
| Inside a PowerPoint deck | native chart via `office_edit` (pptx skill) - editable by the user | pptx skill |
| Inside a Word document | PNG from matplotlib, or a native docx chart via `office_edit` | docx skill |

Install on demand: `pip install matplotlib pandas` (`plotly` and `kaleido` for interactive
/ plotly-to-PNG). Prefer native Office charts when the user will edit the file.

## Non-negotiables

- **Honest scales.** Bar and area charts start at zero. Line charts may zoom, but label
  the axis clearly; never truncate to exaggerate. Same units and scales across small
  multiples that are compared.
- **One value axis.** No dual-axis charts - the alignment of two scales is arbitrary and
  invents correlations. Use two aligned charts, small multiples, or index both series
  (=100 at start).
- **No pie/donut for comparing many or close values.** Use sorted bars. A pie is
  acceptable only for 2-5 parts of a whole with clearly different sizes.
- **No 3D, no rainbow color maps** (jet/rainbow/hsv) for quantities.
- **Color is never the only cue.** Pair it with direct labels, position, markers or line
  style. Check palettes with the script; never eyeball colorblind safety.
- **Color follows the entity.** The same series/category keeps its color across every
  chart on a page and does not change when a filter removes others.
- **Max ~6-8 categorical colors.** Beyond that, fold the tail into "Other", facet into
  small multiples, or highlight the one that matters and gray the rest.
- **Rates need denominators.** Show counts or sample sizes where a percentage could
  mislead (n=12 vs n=12,000).
- **Uncertainty stays with the estimate** (error bars, bands) when it is material, with
  the interval type named (95% CI, min-max, p10-p90).

## Defaults that look professional

- One sans-serif font family; title 14-16 pt, labels 10-12 pt, nothing under ~9 pt.
- Light horizontal gridlines only (or none with direct labels); no chart border; axis
  lines thin and gray.
- Sorted bars (descending) unless the categories have a natural order (time, stages,
  age bands). Horizontal bars when labels are long.
- Number formatting matched to magnitude: `$1.2M`, `48%`, `3.4k`; consistent decimals.
- Highlight-and-mute: the series that carries the message in the accent color, context
  series in gray.
- Annotate the point of the chart (a peak, a policy change, the target line) with a short
  label rather than making the reader hunt.
- Generous whitespace; aspect ratio around 16:9 for time series, taller for ranked bars
  with many categories.

## Dashboards in one sentence each

Lead with 3-5 KPI tiles (value, change vs. comparison period, tiny trend), then the charts
that explain them, then detail tables. One filter row at the top that applies to
everything. Consistent colors and number formats across all panels. Every chart has a
title that says what it shows and its time range. Full guidance and an HTML skeleton:
[references/html-dashboards.md](references/html-dashboards.md).

## Final check (run through before delivering)

- Can someone get the takeaway from the title + chart in 5 seconds?
- Are axes labeled with units, and does the time range appear somewhere?
- Do numbers on the chart match the source data (spot-check 2-3 values)?
- Any overlapping/clipped labels, legend covering data, or unreadable ticks?
- Does it still read in grayscale / for colorblind readers (labels, markers)?
- Is there anything decorative you can delete?

<!-- Adapted in part from GLM skills/visualize (Apache-2.0). Modified for CrabAgent. -->

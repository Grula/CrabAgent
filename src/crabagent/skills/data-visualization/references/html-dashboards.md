# HTML dashboards and interactive charts

Deliver a dashboard as **one self-contained `.html` file**: data inlined as JSON, CSS and
JS inline, at most one charting library loaded from a pinned CDN URL (or none - inline
SVG is enough for most dashboards). The user opens it by double-clicking; no server, no
build step, no network calls for data.

## Layout

1. **Header:** title that names the subject and period ("Sales performance - Q3 2026"),
   one line of context (data source, last refresh, filters applied).
2. **Filter row (optional):** one row of controls above everything; every panel responds
   to the same selection. No per-chart filters.
3. **KPI row:** 3-5 tiles - label, value, change vs. comparison period with direction
   word or sign (not color alone), optional sparkline.
4. **Explanatory charts:** the 2-4 charts that explain the KPIs (trend, breakdown,
   ranking). Full-width trend first; pairs side by side on wide screens, stacked on narrow.
5. **Detail table:** sortable, numbers right-aligned, the rows behind the charts.
6. **Footnote:** definitions of metrics, data caveats, source.

Keep the composition minimal: no decorative cards, invented scores, or controls nobody
asked for. Every panel must answer something.

## Rules for robust charts

- Size each SVG from its container width (`viewBox` set from `clientWidth`, redraw with a
  `ResizeObserver`); never scale a fixed-width drawing down - text becomes unreadably small.
- Reserve room for the longest tick label (often 48-64 px for a y-axis) and for axis
  titles. Measure label widths after drawing (`getBBox()`) and drop or abbreviate labels
  that would overlap; keep at least 4 px between labels.
- Domains from the data (`min`/`max` over all values incl. error bands and targets) with a
  little padding; bars from zero.
- KPI deltas: color by good/bad (a falling churn rate is good), always with an arrow or sign.
- Text never smaller than 11 px; tick labels may be the smallest text on the page.
- Tooltips add precision but are never the only way to read a value: direct labels for
  key points, plus the data table.
- Hit targets for hover/tap at least ~24-32 px even when the mark is small; for dense
  scatter use nearest-point lookup instead of per-dot targets.
- Give every chart an accessible name: `role="img"` + `aria-label` summarizing the takeaway,
  or `<title>`/`<desc>` inside the SVG.
- Support 320 px wide screens: stack panels, reduce ticks, wrap the KPI row.
- Theme with CSS custom properties and `prefers-color-scheme`; resolve colors to concrete
  values before passing them to canvas-based libraries, and redraw on theme change.
- Animate only state changes (filter applied), never on load in a loop; honor
  `prefers-reduced-motion`.
- Use real `<button>`, `<select>`, `<input>` elements so keyboard users can operate filters.
- Keep the file small: aggregate or downsample big data before inlining (a few thousand
  points per chart is plenty).

## Skeleton (vanilla JS + inline SVG)

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sales performance - Q3 2026</title>
<style>
  :root { --bg:#FFFFFF; --panel:#F7F8FA; --ink:#1A1A1A; --muted:#5C6670; --grid:#E3E6EA;
          --accent:#1F5FAD; --context:#9AA5B1; --good:#1F5FAD; --bad:#C2402E; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#121417; --panel:#1B1F24; --ink:#E8EAED; --muted:#A0A7B0; --grid:#2A2F36;
            --accent:#5B9BE6; --context:#5C6670; --good:#5B9BE6; --bad:#F0705C; } }
  * { box-sizing: border-box; }
  body { margin:0; padding:24px; background:var(--bg); color:var(--ink);
         font:14px/1.45 system-ui, -apple-system, "Segoe UI", Inter, Arial, sans-serif; }
  h1 { font-size:20px; margin:0 0 4px; } .sub { color:var(--muted); margin:0 0 20px; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:12px; margin-bottom:20px; }
  .kpi { background:var(--panel); border-radius:8px; padding:14px 16px; }
  .kpi .label { color:var(--muted); font-size:12.5px; }
  .kpi .value { font-size:28px; font-weight:600; margin:2px 0; }
  .kpi .delta { font-size:12.5px; font-variant-numeric:tabular-nums; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(340px, 1fr)); gap:20px; }
  figure { margin:0; } figcaption { font-weight:600; margin-bottom:6px; }
  .chart { width:100%; } .chart text { fill:var(--muted); font-size:12px; }
  table { width:100%; border-collapse:collapse; margin-top:24px; font-variant-numeric:tabular-nums; }
  th, td { padding:6px 8px; border-bottom:1px solid var(--grid); text-align:left; }
  td.num, th.num { text-align:right; }
  .note { color:var(--muted); font-size:12px; margin-top:16px; }
</style>
</head>
<body>
<h1>Sales performance - Q3 2026</h1>
<p class="sub">Source: CRM export, refreshed 2026-10-02. All figures in USD.</p>
<section class="kpis" id="kpis"></section>
<section class="grid">
  <figure><figcaption>Revenue by region, Q3 ($k)</figcaption><div class="chart" id="bars"></div></figure>
</section>
<table id="detail"><thead><tr><th>Region</th><th class="num">Revenue ($k)</th><th class="num">vs Q2</th></tr></thead><tbody></tbody></table>
<p class="note">Revenue = recognized revenue, excluding refunds. Q2 comparison uses the same definition.</p>
<script>
const DATA = {
  kpis: [ {label:"Revenue", value:"$4.43M", delta:+0.12, comp:"vs Q2"},
          {label:"New customers", value:"312", delta:+0.05, comp:"vs Q2"},
          {label:"Churn rate", value:"2.1%", delta:-0.003, comp:"pts vs Q2", pts:true, higherIsBetter:false} ],
  regions: [ {name:"Americas", value:2310, prev:2150}, {name:"EMEA", value:1240, prev:1010},
             {name:"APAC", value:880, prev:845} ]
};
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmtPct = (v, pts) => (v >= 0 ? "+" : "−") + (pts ? Math.abs(v*100).toFixed(1) : Math.abs(v*100).toFixed(0) + "%");

document.getElementById("kpis").innerHTML = DATA.kpis.map(k => `
  <div class="kpi"><div class="label">${k.label}</div><div class="value">${k.value}</div>
  <div class="delta" style="color:${(k.delta >= 0) === (k.higherIsBetter !== false) ? css('--good') : css('--bad')}">
    ${k.delta >= 0 ? "▲" : "▼"} ${fmtPct(k.delta, k.pts)} ${k.comp}</div></div>`).join("");

function drawBars(el, rows) {
  const W = el.clientWidth, rowH = 34, left = 90, right = 64, H = rows.length * rowH + 8;
  const max = Math.max(...rows.map(r => r.value));
  const x = v => left + (W - left - right) * v / max;
  const sorted = [...rows].sort((a, b) => b.value - a.value);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img"
      aria-label="Bar chart: ${sorted.map(r => r.name + ' ' + r.value + 'k').join(', ')}">
    ${sorted.map((r, i) => `
      <text x="${left - 8}" y="${i*rowH + 21}" text-anchor="end">${r.name}</text>
      <rect x="${left}" y="${i*rowH + 6}" width="${x(r.value) - left}" height="${rowH - 14}"
            rx="3" fill="${i === 0 ? css('--accent') : css('--context')}"><title>${r.name}: $${r.value.toLocaleString()}k</title></rect>
      <text x="${x(r.value) + 6}" y="${i*rowH + 21}" style="fill:${css('--ink')}">${r.value.toLocaleString()}</text>`).join("")}
  </svg>`;
}
const barsEl = document.getElementById("bars");
new ResizeObserver(() => drawBars(barsEl, DATA.regions)).observe(barsEl);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => drawBars(barsEl, DATA.regions));

document.querySelector("#detail tbody").innerHTML = DATA.regions.map(r => `
  <tr><td>${r.name}</td><td class="num">${r.value.toLocaleString()}</td>
  <td class="num">${fmtPct(r.value / r.prev - 1)}</td></tr>`).join("");
</script>
</body>
</html>
```

## Using a chart library instead

When you need many chart types, zoom, or rich tooltips, load one library from a pinned
CDN URL and keep everything else inline:
- Plotly: `<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>`,
  or generate the whole file from Python with `fig.write_html(..., include_plotlyjs=True)`
  for a fully offline file.
- Chart.js: `https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js` (canvas -
  pass resolved hex colors, not CSS variables).
- D3 for custom/statistical graphics: `https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js`.

Always pin an exact version. If the dashboard must work offline, inline the library or
use the Python-generated plotly file.

## Verify

- Open/screenshot the file at desktop (~1280 px) and narrow (~380 px) widths if a headless
  browser is available (e.g. `python -m playwright screenshot --viewport-size=1280,900
  file:///abs/path/dash.html shot.png`), then `read` the screenshots.
- Without a browser: re-read the file, check the JSON data matches the source numbers,
  and that every element id referenced in JS exists.
- Check the browser console for errors when you can; a single JS error usually blanks
  every chart.

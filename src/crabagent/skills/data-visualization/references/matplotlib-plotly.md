# matplotlib and plotly recipes

## matplotlib house style

```python
import matplotlib
matplotlib.use("Agg")                         # headless rendering
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

PALETTE = ["#1F5FAD", "#2A9D5C", "#6B4FBB", "#B83E7A", "#E07A1F", "#C2402E", "#0E8A9A", "#8A6D0F"]
GRAY, INK, MUTED, GRID = "#9AA5B1", "#1A1A1A", "#5C6670", "#E3E6EA"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Inter", "Helvetica Neue", "Arial", "DejaVu Sans"],
    "font.size": 11, "axes.titlesize": 15, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.labelsize": 11, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.edgecolor": GRID, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.prop_cycle": matplotlib.cycler(color=PALETTE),
    "legend.frameon": False, "figure.dpi": 100, "savefig.dpi": 200, "savefig.bbox": "tight",
    "lines.linewidth": 2.2,
})

def finish(fig, ax, title, subtitle=None, source=None, path="chart.png"):
    ax.set_title(title, color=INK, pad=24 if subtitle else 10)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=MUTED, fontsize=10.5, va="bottom")
    if source:
        fig.text(0.01, -0.02, source, color=MUTED, fontsize=8.5, ha="left", va="top")
    fig.savefig(path)
    plt.close(fig)
    return path
```

## Line chart with direct labels and a highlight

```python
import pandas as pd
df = pd.DataFrame({"month": pd.date_range("2026-01-01", periods=12, freq="MS"),
                   "EMEA": [3.1, 3.3, 3.2, 3.6, 3.8, 3.9, 4.0, 4.3, 4.4, 4.6, 4.8, 5.1],
                   "Americas": [4.0, 4.1, 4.0, 4.2, 4.1, 4.3, 4.2, 4.4, 4.3, 4.5, 4.4, 4.6],
                   "APAC": [2.0, 2.1, 2.1, 2.2, 2.3, 2.2, 2.4, 2.4, 2.5, 2.5, 2.6, 2.7]})

fig, ax = plt.subplots(figsize=(9, 5))
for name, color, width in (("Americas", GRAY, 1.6), ("APAC", GRAY, 1.6), ("EMEA", PALETTE[0], 2.6)):
    ax.plot(df["month"], df[name], color=color, linewidth=width)
    ax.annotate(f"{name}  ${df[name].iloc[-1]:.1f}M", (df["month"].iloc[-1], df[name].iloc[-1]),
                xytext=(6, 0), textcoords="offset points", va="center",
                color=color if color != GRAY else MUTED, fontweight="bold" if name == "EMEA" else None)
ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f"${v:.0f}M"))
ax.set_ylim(0, None)
ax.margins(x=0.02)
ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b"))
finish(fig, ax, "EMEA passed Americas in September", "Monthly revenue, 2026, $M",
       "Source: finance ledger, Nov 2026", "revenue_by_region.png")
```
Leave room on the right for end labels (`ax.margins(x=...)` or `fig.subplots_adjust(right=0.85)`);
if end labels collide, nudge them apart vertically or drop the less important one.

## Sorted horizontal bars with value labels

```python
s = pd.Series({"Search": 412, "Email": 238, "Social": 190, "Referral": 96, "Other": 41}).sort_values()
fig, ax = plt.subplots(figsize=(8, 0.5 * len(s) + 1.2))
bars = ax.barh(s.index, s.values, color=PALETTE[0], height=0.62)
ax.bar_label(bars, labels=[f"{v:,}" for v in s.values], padding=4, color=INK, fontsize=10)
ax.grid(False); ax.spines["bottom"].set_visible(False); ax.set_xticks([])
ax.tick_params(axis="y", length=0)
finish(fig, ax, "Search drives 42% of sign-ups", "Sign-ups by channel, Oct 2026", path="channels.png")
```

## Other frequent forms

```python
# Small multiples with shared axes
fig, axes = plt.subplots(2, 3, figsize=(11, 6), sharex=True, sharey=True)
for ax, (name, g) in zip(axes.flat, data.groupby("segment")):
    ax.plot(g["date"], g["value"], color=PALETTE[0]); ax.set_title(name, fontsize=11)

# Heatmap with a sequential map and annotations
im = ax.imshow(matrix, cmap="Blues", aspect="auto")
fig.colorbar(im, ax=ax, shrink=0.8, label="Retention %")

# Diverging bars around zero
colors = [PALETTE[0] if v >= 0 else PALETTE[4] for v in deltas]
ax.barh(labels, deltas, color=colors); ax.axvline(0, color=INK, linewidth=0.8)

# Uncertainty band
ax.plot(x, mean, color=PALETTE[0]); ax.fill_between(x, lo, hi, color=PALETTE[0], alpha=0.18, linewidth=0)

# Percent axis
ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))       # values stored as fractions
```

Output tips: PNG at 200 dpi for documents/slides (size the figure to the target, e.g. 10x5.6 in
for a 16:9 slide area); SVG for the web or for print; `transparent=True` only when the
background is known. For a dark slide, re-run with dark rcParams instead of inverting.
CJK labels need a CJK font in `font.sans-serif` (e.g. "Noto Sans CJK SC").

## plotly (interactive HTML)

```python
import plotly.graph_objects as go
import plotly.io as pio

pio.templates["clean"] = go.layout.Template(layout=dict(
    font=dict(family="Inter, Helvetica, Arial, sans-serif", size=13, color="#1A1A1A"),
    colorway=PALETTE, paper_bgcolor="white", plot_bgcolor="white",
    xaxis=dict(showgrid=False, linecolor="#C9CED6", ticks="outside", tickcolor="#C9CED6"),
    yaxis=dict(gridcolor="#E3E6EA", zeroline=False),
    legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom"),
    margin=dict(l=60, r=30, t=70, b=50), hoverlabel=dict(font_size=12)))
pio.templates.default = "clean"

fig = go.Figure()
for name in ["EMEA", "Americas", "APAC"]:
    fig.add_trace(go.Scatter(x=df["month"], y=df[name], name=name, mode="lines",
                             hovertemplate="%{x|%b %Y}: $%{y:.1f}M<extra>" + name + "</extra>"))
fig.update_layout(title=dict(text="<b>Monthly revenue by region</b><br><sup>2026, $M</sup>"),
                  yaxis=dict(rangemode="tozero", tickprefix="$", ticksuffix="M"), hovermode="x unified")
fig.write_html("revenue.html", include_plotlyjs="cdn", full_html=True)   # or include_plotlyjs=True for offline
# static image: pip install kaleido ; fig.write_image("revenue.png", scale=2)
```
`include_plotlyjs=True` embeds the library (~3.5 MB) so the file works offline;
`"cdn"` keeps it small but needs internet.

## Verify

Open the PNG with `read`. For HTML, screenshot it with a headless browser if available,
or at least re-open the file and confirm data/labels are present. Check: title states the
point, axes labeled with units, no overlaps or clipped labels, legend not covering data,
colors consistent with other charts in the same deliverable.

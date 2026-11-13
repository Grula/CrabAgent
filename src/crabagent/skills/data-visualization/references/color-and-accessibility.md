# Color and accessibility

## Four color jobs - decide which one before choosing colors

| Job | Encodes | Structure |
|---|---|---|
| Emphasis | "this one matters" | one accent hue + neutral gray for everything else |
| Categorical | identity of series | distinct hues in a fixed order, assigned in sequence, max ~6-8 |
| Sequential | magnitude | one hue, light (low) to dark (high) on light backgrounds |
| Diverging | above/below a meaningful midpoint | two contrasting hues, neutral gray/white middle, symmetric steps |

Status colors (good / warning / bad) are a separate, reserved set; never reuse them as
"series 4", and always pair them with a word or icon.

If unordered categories are shown as bars, they get **one** color - darkening bigger bars
just repeats what bar length already shows. Ordered categories (stages, tiers, age
bands) may use a sequential ramp.

## Default palettes (validated with scripts/check_palette.py)

Categorical, light backgrounds (all >= 3:1 contrast on white; adjacent pairs distinguishable
under protanopia, deuteranopia and tritanopia):
```
#1F5FAD blue   #2A9D5C green   #6B4FBB purple   #B83E7A magenta
#E07A1F orange #C2402E red     #0E8A9A teal     #8A6D0F olive
```
Categorical, dark backgrounds (same hue order, lightened; >= 5:1 on #121417):
```
#5B9BE6  #2E9A5E  #A48BF5  #E87BB3  #F2B13F  #F0705C  #3FB6C8  #B39B6B
```
Neutral for context/de-emphasis: `#9AA5B1` (light bg), `#5C6670` (dark bg). Text:
`#1A1A1A` / `#5C6670` on light; `#E8EAED` / `#A0A7B0` on dark. Gridlines `#E3E6EA` / `#2A2F36`.

For scatter plots, maps and small multiples (any two colors can touch) keep to the first
three colors, or check with `--pairs all`.

Well-known alternatives: Okabe-Ito (`#0072B2 #E69F00 #009E73 #CC79A7 #56B4E9 #D55E00
#F0E442`) - colorblind-friendly, but its yellow/orange/sky-blue are too light for thin
lines on white (< 3:1); use them for fills with labels. Tableau 10 is familiar but several
colors are below 3:1 on white.

Sequential: matplotlib `Blues`, `Greens`, `viridis`, `cividis` (cividis is designed for
CVD). Diverging: `RdBu`, `PuOr`, `BrBG` (centered with `TwoSlopeNorm(vcenter=...)`).
Avoid `jet`, `rainbow`, `hsv`, `Spectral` for quantities.

## Validate - don't eyeball

```bash
python scripts/check_palette.py "#1F5FAD,#E07A1F,#2A9D5C" --background "#FFFFFF"
python scripts/check_palette.py "<colors>" --background "#121417"          # dark theme
python scripts/check_palette.py "<colors>" --pairs all                     # scatter / maps
```
It reports WCAG contrast of each color against the background, and the smallest
perceptual distance between colors for normal vision and simulated protanopia,
deuteranopia and tritanopia. Ratings: GOOD (color alone works), MARGINAL (add direct
labels / markers / dash styles), POOR (change the palette or reduce series). Exit code 1
= fix before shipping. Reorder colors to separate weak adjacent pairs; swapping order is
often enough.

## Accessibility checklist

- Marks (lines, bars, dots) >= 3:1 contrast against the background; text >= 4.5:1.
- Color never the only channel: direct labels, distinct markers (o, s, ^, D) or line
  styles, patterns for print.
- Red/green as the only good/bad signal fails for ~8% of men - use blue/orange or add
  +/- signs and icons.
- Minimum text ~10-11 px on screen, 8-9 pt in print.
- Provide the numbers: a data table, alt text that states the takeaway and key values
  ("Line chart: revenue rose from $3.1M in Q1 to $4.9M in Q4; Q4 was the highest quarter").
- Interactive charts: keyboard-reachable controls, tooltips that are not the only way to
  read values, visible focus, honor `prefers-reduced-motion`.
- Dark mode is a separate, validated palette - not an automatic inversion.

## Typography and ink

- One sans-serif family (system UI stack, Inter, Source Sans, Arial/Helvetica).
- Text in neutral ink, not in series colors; a colored swatch or line next to the label
  carries identity.
- Tabular (monospaced) figures where numbers align in columns/axes; proportional figures
  for big standalone numbers.
- Hierarchy: title (bold, 14-16 pt) > subtitle/units (regular, muted) > labels > footnote/source.

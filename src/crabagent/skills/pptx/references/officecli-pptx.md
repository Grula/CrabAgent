# OfficeCLI cheat sheet - PowerPoint (.pptx)

Starting point only; the installed CLI is authoritative: `office_help(file_format="pptx")`,
or bash `officecli help pptx <element>` / `officecli help pptx set shape`.

## Tool mapping

- `office_read(mode="outline")` - slide list with titles and shape summaries;
  `mode="text"` - all text incl. tables; `mode="stats"` - counts (pictures missing alt...).
- `office_query("/slide[2]", depth=2)` - shapes with ids, names, positions, text.
- `office_query('shape:contains("TODO")', mode="selector")` - search.
- `office_edit` / `office_batch_edit` - set / add / remove / move / swap.
- `office_render` - HTML rendering of all slides.

Bash-only: `officecli add deck.pptx / --from '/slide[3]'` (clone a slide with all its
relationships; add `--after '/slide[3]'` to place it), `officecli view deck.pptx issues`,
`officecli view deck.pptx annotated`, `officecli view deck.pptx screenshot --page 2 -o s2.png`
(needs a headless browser; `--grid 3` for a contact sheet), `officecli validate deck.pptx`,
`officecli merge template.pptx out.pptx --data data.json` (`{{key}}` placeholders),
`officecli close deck.pptx`.

## Paths

```
/                              presentation (slide size, defaultFont, theme)
/slide[3]                      slide (layout, background, transition, hidden)
/slide[3]/shape[2]             shape by position (shape[1] is often the title placeholder)
/slide[3]/shape[@id=5]         stable id    /slide[3]/shape[@name=Title]   by name
/slide[3]/shape[2]/paragraph[1]/run[2]
/slide[3]/table[1]/tr[2]/tc[1]
/slide[3]/chart[1]   /slide[3]/picture[1]   /slide[3]/notes
/slide[last()]                 last slide
/theme   /slidemaster[1]
```
Move a slide: `move` `/slide[5]` with `props.index` (0-based) or `after`/`before`
`/slide[2]`. Swap two slides: `swap` with `props.with`. Delete: `remove /slide[4]`.

## Slide

`add` to `/` type `slide`: `layout` (`blank`, `Title Slide`, `Title and Content`,
`Title Only`, ... or the template's layout names), `title`, `background` (hex, gradient
`1E2761-CADCFC-180`, or `image:/abs/path.jpg`), `hidden`. `set`: `transition`
(fade/push/wipe/morph), `background`.

## Shape / text box

`add` type `shape` (or `textbox`) props:
`text`, `x`, `y`, `width`, `height` (units: `in`, `cm`, `pt`, EMU), `name`,
`geometry` / `preset` (rect, roundRect, ellipse, rightArrow, chevron, ...), `fill`,
`line` / `lineColor` / `lineWidth` / `lineDash` (`none` to remove the outline), `opacity`,
`rotation`, `shadow`, `font`, `size`, `bold`, `italic`, `underline`, `color`,
`align` (left/center/right/justify), `valign` (top/middle/bottom), `margin`
(inner padding - set `0` to align text with other objects), `list` (bullet/numbered,
set at shape level), `lineSpacing`, `spaceBefore`, `spaceAfter`, `autofit`, `link`.

Paragraphs/runs: `add` type `paragraph` under a shape (`text`, `level`, `bold`, `size`,
`color`, `align`); `add` type `run` under a paragraph for mixed formatting.

## Table

`add` type `table`: `rows`, `cols` or `data` (2-D array or `"a,b;c,d"`), `x`, `y`,
`width`, `height`, `style` (`medium1..4`, `light1..3`, `dark1..2` or a built-in style
name), `headerFill`, `bodyFill`, `border.all` (`single;1pt;D0D7E1`), `size`.
Cells: `set /slide[N]/table[1]/tr[r]/tc[c]` with `text`, `fill`, `bold`, `color`, `size`,
`align`, `valign`, `padding`; merge with `merge.right` / `colspan`.

## Chart (native, editable)

`add` type `chart`:
```json
{"chartType":"column","title":"Revenue by region ($M)","categories":"Q1,Q2,Q3,Q4",
 "data":"East:120,135,148,162;West:95,108,115,128","x":"0.6in","y":"1.4in",
 "width":"7in","height":"4.6in","colors":"1F5FAD,E07A1F","legend":"bottom",
 "dataLabels":"true","labelPos":"outsideEnd"}
```
`chartType`: column, columnStacked, columnPercentStacked, bar, barStacked, line, area,
pie, doughnut, scatter, bubble, radar, combo, waterfall, stock. Other props: `gridlines`,
`plotFill`, `gapwidth`, `overlap`, `min`, `max`, `majorUnit`, `title.size`, `title.color`,
`referenceline`, `secondaryaxis` (avoid dual axes unless asked - see data-visualization).
On stacked charts keep data labels inside the bars (`labelPos` center / insideEnd).
Series are fixed at creation; to change series, remove and re-add the chart.

## Picture, connector, notes

- Picture: `add` type `picture` `src` (absolute path), `x`, `y`, `width` (or `height`),
  `alt`, `crop`, `link`. SVG is accepted.
- Connector / arrow between shapes: `add` type `connector` with `from` / `to` as full
  paths (`/slide[2]/shape[@name=Box1]`), optional `fromSide`/`toSide`, `lineColor`, `tailEnd`.
- Speaker notes: `add` type `notes` `{"text":"..."}` under `/slide[N]` (or `set` it).
- Comment: `add` type `comment` `{"author":"...","text":"...","x":"20cm","y":"3cm"}`.

## Theme

`set /theme` with `accent1`..`accent6`, `headingFont`, `bodyFont` - lets the whole deck
(and charts using theme colors) follow one palette.

## Values

Colors hex without `#`. Booleans `"true"`/`"false"`. Text with `$` is safe in tool calls
(no shell), but in bash use single quotes: `--prop text='$15M'`.

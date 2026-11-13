# python-pptx recipes

`pip install python-pptx` if `import pptx` fails. It reads and writes `.pptx`, but cannot
render, cannot duplicate slides natively, and does not compute autofit.

## New deck from scratch

```python
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

prs = Presentation()                          # or Presentation("template.pptx")
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BLANK = prs.slide_layouts[6]                  # check names: [l.name for l in prs.slide_layouts]

def add_text(slide, text, x, y, w, h, size=18, bold=False, color="1A1A1A", align=PP_ALIGN.LEFT,
             font="Calibri"):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = 0                   # align with other objects at the same x
    lines = text if isinstance(text, list) else [text]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = line
        run.font.size, run.font.bold, run.font.name = Pt(size), bold, font
        run.font.color.rgb = RGBColor.from_string(color)
    return box

s = prs.slides.add_slide(BLANK)
add_text(s, "Revenue grew 18% on enterprise renewals", 0.6, 0.4, 12.1, 0.9, size=30, bold=True)

data = CategoryChartData()
data.categories = ["Q1", "Q2", "Q3", "Q4"]
data.add_series("FY25", (3.1, 3.4, 3.6, 3.9))
data.add_series("FY26", (3.8, 4.1, 4.4, 4.9))
gframe = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(1.5),
                            Inches(7.6), Inches(5.2), data)
chart = gframe.chart
chart.has_legend, chart.legend.position, chart.legend.include_in_layout = True, XL_LEGEND_POSITION.BOTTOM, False
for series, hex_ in zip(chart.series, ("9AA5B1", "1F5FAD")):
    series.format.fill.solid(); series.format.fill.fore_color.rgb = RGBColor.from_string(hex_)
chart.value_axis.has_major_gridlines = True
chart.value_axis.major_gridlines.format.line.color.rgb = RGBColor.from_string("E3E6EA")
chart.value_axis.tick_labels.font.size = chart.category_axis.tick_labels.font.size = Pt(12)

add_text(s, ["Q4 was the strongest quarter", "EMEA contributed half of the growth"],
         8.6, 1.6, 4.1, 3.0, size=16)
s.notes_slide.notes_text_frame.text = "Lead with the Q4 beat."
prs.save("deck.pptx")
```

Bullets in a text box: set the paragraph level (`p.level = 1`) and use a layout/body
placeholder whose text style already has bullets, or add bullet XML (`a:buChar`) to `pPr`.
Never type "•" characters.

Table:
```python
s2 = prs.slides.add_slide(BLANK)
rows, cols = 4, 3
tbl = s2.shapes.add_table(rows, cols, Inches(0.6), Inches(1.5), Inches(7), Inches(2)).table
for c, width in enumerate((3.0, 2.0, 2.0)):
    tbl.columns[c].width = Inches(width)
tbl.cell(0, 0).text = "Region"
cell = tbl.cell(1, 1); cell.text = "1,240"
cell.text_frame.paragraphs[0].alignment = PP_ALIGN.RIGHT
cell.fill.solid(); cell.fill.fore_color.rgb = RGBColor.from_string("F3F6FA")
cell.vertical_anchor = MSO_ANCHOR.MIDDLE
```

Picture with alt text: `pic = s.shapes.add_picture("chart.png", Inches(1), Inches(1.5), width=Inches(6))`
(give width or height, not both), then `pic._element.nvPicPr.cNvPr.set("descr", "Revenue chart")`.

## Inventory an existing deck

`python scripts/pptx_inventory.py deck.pptx` prints every shape (groups included) with
id, name, placeholder type, position, font sizes and text, plus warnings. Use it as the
source of truth for which shape holds which text before replacing anything. Rendered text
can hide soft line breaks (`\x0b`) and non-breaking spaces; match on the inventory text.

## Replace text while keeping formatting

```python
def iter_text_frames(shapes):
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    for sh in shapes:
        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from iter_text_frames(sh.shapes)
        elif sh.has_text_frame:
            yield sh, sh.text_frame
        elif getattr(sh, "has_table", False) and sh.has_table:
            for row in sh.table.rows:
                for cell in row.cells:
                    yield sh, cell.text_frame

def set_paragraph_text(p, new_text):
    """Write into the first run (keeps its font/color/size) and drop the other runs."""
    if not p.runs:
        p.add_run().text = new_text
        return
    p.runs[0].text = new_text
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)

def norm(t):
    return t.replace("\x0b", " ").replace("\xa0", " ").strip()

prs = Presentation("in.pptx")
mapping = {"Q3 2025 Results": "Q3 2026 Results"}
for idx, slide in enumerate(prs.slides, 1):
    for shape, tf in iter_text_frames(slide.shapes):
        for p in tf.paragraphs:
            key = norm("".join(r.text for r in p.runs))
            if key in mapping:
                set_paragraph_text(p, mapping[key])
prs.save("out.pptx")
```
- `text_frame.text = ...` or `paragraph.text = ...` collapses runs and loses formatting -
  avoid on styled text.
- Scope short or numeric replacements to a slide/shape; a global "18" -> "21" corrupts
  unrelated text.
- Keep each replacement close to the original length; a label box (badges, KPI labels,
  table headers) holds a short phrase, not a sentence.

## Slide operations

```python
from copy import deepcopy
from pptx.oxml.ns import qn

def delete_slide(prs, index):                 # 0-based; delete from the highest index down
    sld_id = prs.slides._sldIdLst[index]
    prs.part.drop_rel(sld_id.get(qn("r:id")))
    prs.slides._sldIdLst.remove(sld_id)

def move_slide(prs, old, new):
    lst = prs.slides._sldIdLst
    el = lst[old]; lst.remove(el); lst.insert(new, el)

def duplicate_slide(prs, index):
    """Copy a slide's shapes onto a new slide (same layout), re-pointing image/chart/link rels."""
    src = prs.slides[index]
    dst = prs.slides.add_slide(src.slide_layout)
    for ph in list(dst.placeholders):
        ph._element.getparent().remove(ph._element)
    rid_map = {}
    for rel in src.part.rels.values():
        if "notesSlide" in rel.reltype or "slideLayout" in rel.reltype:
            continue
        if rel.is_external:
            rid_map[rel.rId] = dst.part.rels.get_or_add_ext_rel(rel.reltype, rel.target_ref)
        else:
            rid_map[rel.rId] = dst.part.rels.get_or_add(rel.reltype, rel.target_part)
    for shape in src.shapes:
        el = deepcopy(shape._element)
        for node in el.iter():
            for attr in (qn("r:embed"), qn("r:id"), qn("r:link")):
                if node.get(attr) in rid_map:
                    node.set(attr, rid_map[node.get(attr)])
        dst.shapes._spTree.insert_element_before(el, "p:extLst")
    if src.has_notes_slide:
        dst.notes_slide.notes_text_frame.text = src.notes_slide.notes_text_frame.text
    return dst
```
A duplicated chart still points at the same chart part - editing one changes both. For
independent copies, prefer `officecli add deck.pptx / --from '/slide[N]'`, which clones
all related parts, or rebuild the chart. Do structural work (duplicate, delete, reorder)
before editing content.

## Template workflow

1. Inventory the template and render it to see each layout/slide.
2. Map each content section to a template slide; vary layouts instead of repeating one.
3. Duplicate/delete/reorder slides to match the outline.
4. Replace text shape by shape within each slot's budget; delete unused slots entirely
   (the whole group: shape, icon, text).
5. Replace images by inserting the new picture at the old one's position/size and removing
   the old one (or `set` the picture `src` with office_edit).
6. Inventory again (no PLACEHOLDER/EMPTY warnings), render, inspect.

## Speaker notes, sections, metadata

```python
slide.notes_slide.notes_text_frame.text = "Talking points..."
prs.core_properties.title, prs.core_properties.author = "FY26 Review", "Strategy team"
```
python-pptx has no API for sections; use OfficeCLI or leave them as in the template.

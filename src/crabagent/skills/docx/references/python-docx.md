# python-docx recipes

Install on demand: `pip install python-docx` (import name `docx`). Use it when the office
tools cannot express something, or for bulk generation from data. It can create and edit
`.docx` (not `.doc`), but cannot render or recalculate fields.

## Skeleton with styles set up properly

```python
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

doc = Document()                      # or Document("template.docx") to inherit its styles

sec = doc.sections[0]                 # US Letter; use Cm(21.0) x Cm(29.7) for A4
sec.page_width, sec.page_height = Inches(8.5), Inches(11)
for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
    setattr(sec, side, Inches(1))

normal = doc.styles["Normal"]
normal.font.name, normal.font.size = "Calibri", Pt(11)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")   # also set the East-Asian slot
normal.paragraph_format.space_after = Pt(6)
normal.paragraph_format.line_spacing = 1.15

for level, size in ((1, 16), (2, 13), (3, 11.5)):
    st = doc.styles[f"Heading {level}"]
    st.font.name, st.font.size, st.font.bold = "Calibri", Pt(size), True
    st.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    st.paragraph_format.space_before, st.paragraph_format.space_after = Pt(12), Pt(4)
    st.paragraph_format.keep_with_next = True

doc.add_heading("Quarterly Review", level=0)          # Title style
doc.add_heading("Summary", level=1)
p = doc.add_paragraph("Revenue grew ")
p.add_run("18%").bold = True
p.add_run(" year over year.")
doc.add_paragraph("Enterprise renewals", style="List Bullet")
doc.add_paragraph("Second step", style="List Number")
doc.save("report.docx")
```

Rules that avoid the usual defects:
- Headings: always `add_heading` / built-in "Heading N" styles - a bold Normal paragraph
  does not appear in the TOC or navigation pane.
- Lists: "List Bullet"/"List Number" styles (or a numbering definition), never a typed `•`.
- New lines: a new paragraph per line; `run.add_break()` only for a soft line break.
- Page break: `doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)`, or
  `paragraph_format.page_break_before = True` on the next heading.
- Editing existing text: change `run.text`; assigning `paragraph.text` wipes run formatting.

## Tables with predictable widths

```python
rows = [("Quarter", "Revenue", "Growth"), ("Q1", "$1.2M", "12%"), ("Q2", "$1.4M", "17%")]
widths = [Inches(2.5), Inches(2.0), Inches(2.0)]              # sum = text width (6.5in on Letter, 1in margins)
table = doc.add_table(rows=len(rows), cols=3)
table.style = "Table Grid"                                     # or a style from the template
table.autofit = False
for r, values in enumerate(rows):
    for c, value in enumerate(values):
        cell = table.cell(r, c)
        cell.width = widths[c]                                 # set on every cell; Word reads cell widths
        cell.text = value
        para = cell.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.RIGHT if c else WD_ALIGN_PARAGRAPH.LEFT

def shade(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")       # "clear" + fill; "solid" paints the cell black in some viewers
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)

for cell in table.rows[0].cells:
    shade(cell, "1F4E79")
    run = cell.paragraphs[0].runs[0]
    run.font.bold, run.font.color.rgb = True, RGBColor(0xFF, 0xFF, 0xFF)

# repeat header row on each page, and keep rows from splitting across pages
trPr = table.rows[0]._tr.get_or_add_trPr()
hdr = OxmlElement("w:tblHeader"); hdr.set(qn("w:val"), "true"); trPr.append(hdr)
for row in table.rows:
    cant = OxmlElement("w:cantSplit"); cant.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(cant)
```

Merge cells: `table.cell(0, 0).merge(table.cell(0, 2))`.

## Images and captions

```python
doc.add_picture("chart.png", width=Inches(6))      # give width only - height follows the aspect ratio
doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
cap = doc.add_paragraph("Figure 1. Revenue by quarter", style="Caption")
```
Set alt text: `doc.inline_shapes[-1]._inline.docPr.set("descr", "Bar chart of revenue by quarter")`.
Keep images within the text width (page width minus margins).

## Headers, footers, page numbers

```python
def add_field(paragraph, instr):                 # PAGE, NUMPAGES, DATE \@ "d MMMM yyyy", TOC \o "1-3" \h \z \u
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, instr), ("separate", None), (None, "1"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar"); el.set(qn("w:fldCharType"), kind)
        elif text == instr:
            el = OxmlElement("w:instrText"); el.set(qn("xml:space"), "preserve"); el.text = f" {instr} "
        else:
            el = OxmlElement("w:t"); el.text = text   # placeholder result shown until fields update
        run._r.append(el)

footer = doc.sections[0].footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
footer.add_run("Page "); add_field(footer, "PAGE"); footer.add_run(" of "); add_field(footer, "NUMPAGES")
doc.sections[0].header.paragraphs[0].text = "Acme Corp - Confidential"
```

Different first page: `sec.different_first_page_header_footer = True` (then fill
`sec.first_page_footer`). New section (e.g. landscape appendix):
`new = doc.add_section(); new.orientation = WD_ORIENT.LANDSCAPE;
new.page_width, new.page_height = sec.page_height, sec.page_width`.

## Table of contents

Insert a TOC field where it should appear (usually after the title page), followed by a
page break, then ask Word to refresh fields on open:

```python
toc_para = doc.add_paragraph()
add_field(toc_para, 'TOC \\o "1-3" \\h \\z \\u')
settings = doc.settings.element
upd = OxmlElement("w:updateFields"); upd.set(qn("w:val"), "true"); settings.append(upd)
```
The TOC shows placeholder text until the document is opened in Word (or refreshed with
LibreOffice / `officecli refresh`). Tell the user that page numbers populate on open.

## Templates and placeholder filling

Open the template with `Document("template.docx")` so its styles, headers and numbering
carry over; delete sample paragraphs instead of building from a blank document.

Replacing `{{name}}` style tokens: a token is often split across runs. Run
`python scripts/docx_xml_tools.py merge-runs in.docx -o merged.docx` first, then:

```python
def replace_everywhere(doc, mapping):
    def fix(paragraphs):
        for p in paragraphs:
            for run in p.runs:
                for key, value in mapping.items():
                    if key in run.text:
                        run.text = run.text.replace(key, value)   # keeps the run's formatting
    fix(doc.paragraphs)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                fix(cell.paragraphs)
    for s in doc.sections:
        for part in (s.header, s.footer, s.first_page_header, s.first_page_footer):
            fix(part.paragraphs)
```
Alternatively `officecli merge template.docx out.docx --data '{"name":"Acme"}'` handles
split tokens, tables, headers and footers in one step.

After filling, scan for leftovers: `grep -E '\{\{|\}\}'` on `office_read` output.

## Reading

```python
for i, p in enumerate(doc.paragraphs):
    print(i, p.style.name, p.text)
for t in doc.tables:
    print([[c.text for c in row.cells] for row in t.rows])
```
`doc.paragraphs` skips text inside tables, text boxes and headers; `office_read` or
`pandoc -t markdown` give a fuller picture.

# Creating PDFs with reportlab

`pip install reportlab`. Use Platypus (flowables) for documents; use the raw canvas only
for fixed-position artwork (certificates, labels, stamps).

## Report template: styles, header/footer, page numbers, table, image

```python
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

PAGE = A4                                   # letter for US users
MARGIN = 2 * cm
ACCENT = colors.HexColor("#1F4E79")

styles = getSampleStyleSheet()
body = ParagraphStyle("Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=10.5,
                      leading=14.5, spaceAfter=6)
h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16,
                    leading=20, textColor=ACCENT, spaceBefore=12, spaceAfter=6, keepWithNext=1)
h2 = ParagraphStyle("H2", parent=h1, fontSize=12.5, leading=16, spaceBefore=10)
cell = ParagraphStyle("Cell", parent=body, fontSize=9, leading=11, spaceAfter=0)
cell_r = ParagraphStyle("CellR", parent=cell, alignment=TA_RIGHT)
head = ParagraphStyle("Head", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
head_r = ParagraphStyle("HeadR", parent=head, alignment=TA_RIGHT)

def decorate(canv, doc):
    canv.saveState()
    w, h = PAGE
    canv.setFont("Helvetica", 8); canv.setFillColor(colors.grey)
    canv.drawString(MARGIN, h - 1.2 * cm, "Acme Corp - Quarterly Review")
    canv.drawRightString(w - MARGIN, 1.2 * cm, f"Page {doc.page}")
    canv.setStrokeColor(colors.HexColor("#D0D7E1")); canv.setLineWidth(0.5)
    canv.line(MARGIN, h - 1.4 * cm, w - MARGIN, h - 1.4 * cm)
    canv.restoreState()

doc = SimpleDocTemplate("report.pdf", pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN,
                        topMargin=2.2 * cm, bottomMargin=2 * cm,
                        title="Quarterly Review", author="Finance")
avail = PAGE[0] - 2 * MARGIN                # usable width - every table/image must fit inside

story = [Paragraph("Quarterly Review", styles["Title"]),
         Paragraph("Revenue grew <b>18%</b> year over year, driven by enterprise renewals.", body),
         Paragraph("Results by region", h1)]

rows = [["Region", "Revenue ($k)", "Growth"],
        ["EMEA", "1,240", "12.4%"], ["Americas", "2,310", "21.0%"], ["APAC", "880", "9.8%"]]
def style_for(r, c):                        # Paragraph cells ignore TableStyle TEXTCOLOR/FONTNAME
    return (head if c == 0 else head_r) if r == 0 else (cell if c == 0 else cell_r)
data = [[Paragraph(str(v), style_for(r, c)) for c, v in enumerate(row)] for r, row in enumerate(rows)]
table = Table(data, colWidths=[avail * f for f in (0.5, 0.25, 0.25)], repeatRows=1)
table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6FA")]),
    ("LINEBELOW", (0, 0), (-1, 0), 0.75, ACCENT),
    ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#D0D7E1")),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
]))
story += [table, Spacer(1, 8)]

# image scaled to the available width, aspect ratio preserved, caption kept with it
# from reportlab.lib.utils import ImageReader
# iw, ih = ImageReader("chart.png").getSize(); w = avail; h = w * ih / iw
# story.append(KeepTogether([Image("chart.png", width=w, height=h),
#                            Paragraph("Figure 1. Revenue by quarter", styles["Italic"])]))

story += [PageBreak(), Paragraph("Appendix", h1)]
doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
```

"Page X of Y" needs two passes: subclass `canvas.Canvas` to collect page states, or build
once to count pages and again with the total.

## Layout rules that prevent the usual defects

- **Width budget:** `sum(colWidths) <= avail`. Give columns proportional widths; never
  leave reportlab to auto-size long text columns.
- **Wrap cell text:** put text in `Paragraph` objects - plain strings never wrap and run
  through the neighbouring column. Paragraph cells take font and color from their
  ParagraphStyle; `TEXTCOLOR`/`FONTNAME` in a TableStyle only affect plain-string cells.
- **Long tables:** `repeatRows=1` repeats the header on each page; tables split
  automatically between rows.
- **Keep headings with content:** `keepWithNext=1` on heading styles; `KeepTogether` for
  figure + caption or a small table + its title.
- **Images:** scale to fit `avail` (and the remaining frame height); never place at native
  pixel size.
- **No manual spacing hacks:** use `spaceBefore/After` and `Spacer`, not empty paragraphs.
- **Escaping:** Paragraph text is mini-HTML - escape `&`, `<`, `>` in user data
  (`xml.sax.saxutils.escape`). Use `<b>`, `<i>`, `<font color="#C00000">`, `<sub>`, `<super>`,
  `<br/>`, `<a href="...">`.

## Fonts and non-Latin text

Built-in fonts (Helvetica, Times, Courier) only cover Latin-1. For other scripts, symbols
(e.g. "✓", "→", "€" in some fonts) or a brand font, register a TTF:
```python
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
from reportlab.lib.fonts import addMapping
addMapping("DejaVu", 0, 0, "DejaVu"); addMapping("DejaVu", 1, 0, "DejaVu-Bold")   # so <b> works
```
Find installed fonts with `fc-list : family file` (Linux/macOS). For Chinese/Japanese/Korean
use a CJK font (Noto Sans CJK, Source Han Sans, or reportlab's CID font
`UnicodeCIDFont("STSong-Light")`), and give CJK text extra width - roughly one em per
character. Missing glyphs render as black boxes - check the rendered page.

## Charts in PDFs

Render charts with matplotlib to PNG at 200-300 dpi (or SVG -> via `svglib` to keep them
vector) sized to the column width, then add as `Image`. Follow the data-visualization skill
if available for chart design.

## Other routes

- **Word-like documents users may edit:** build `.docx` (docx skill), then
  `soffice --headless --convert-to pdf file.docx`.
- **HTML/CSS layouts:** if Playwright is installed: `page.goto(f"file://{path}")`,
  `page.pdf(path="out.pdf", format="A4", print_background=True)`. Avoid
  `backdrop-filter` and `overflow:hidden` on page containers (content gets dropped/clipped);
  use `@page { size: A4; margin: 0 }` plus explicit page `div`s for fixed layouts.
- **Fixed-position output (certificates, labels):** canvas API with explicit coordinates
  (origin bottom-left, units in points: 72 pt = 1 in).

Always render the result and inspect it - see the verification steps in SKILL.md.

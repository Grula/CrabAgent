# Advanced PDF operations

## pdfplumber: tables, coordinates, regions

```python
import pdfplumber
with pdfplumber.open("in.pdf") as pdf:
    page = pdf.pages[0]
    # ruled tables
    tables = page.extract_tables({"vertical_strategy": "lines", "horizontal_strategy": "lines",
                                  "snap_tolerance": 3, "intersection_tolerance": 15})
    # borderless tables: infer columns from text alignment
    tables = page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text"})
    # words with positions (top-left origin, points)
    for w in page.extract_words(keep_blank_chars=False, extra_attrs=["size", "fontname"])[:20]:
        print(w["text"], round(w["x0"]), round(w["top"]), w["size"])
    # text inside a region (x0, top, x1, bottom)
    print(page.within_bbox((50, 100, 550, 300)).extract_text())
    # debug what the table finder sees
    page.to_image(resolution=120).debug_tablefinder().save("debug_tables.png")
```

Tables split across pages: extract per page, drop repeated header rows, concatenate:
```python
import pandas as pd
frames, header = [], None
with pdfplumber.open("report.pdf") as pdf:
    for page in pdf.pages:
        for t in page.extract_tables():
            if not t:
                continue
            if header is None:
                header, rows = t[0], t[1:]
            else:
                rows = t[1:] if t[0] == header else t
            frames.append(pd.DataFrame(rows, columns=header))
df = pd.concat(frames, ignore_index=True)
num = lambda s: pd.to_numeric(s.str.replace(r"[,$%\s]", "", regex=True)
                              .str.replace(r"^\((.*)\)$", r"-\1", regex=True), errors="coerce")
```

## pypdfium2: fast rendering and text

```python
import pypdfium2 as pdfium
pdf = pdfium.PdfDocument("in.pdf")
for i, page in enumerate(pdf, 1):
    page.render(scale=2).to_pil().save(f"page_{i}.png")       # scale 2 ~ 144 dpi
    text = page.get_textpage().get_text_range()
```

## Watermark / stamp every page

```python
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

src = PdfReader("in.pdf")
w0, h0 = float(src.pages[0].mediabox.width), float(src.pages[0].mediabox.height)
buf = BytesIO()
c = canvas.Canvas(buf, pagesize=(w0, h0))
c.setFillColor(Color(0.6, 0.6, 0.6, alpha=0.25)); c.setFont("Helvetica-Bold", 60)
c.translate(w0 / 2, h0 / 2); c.rotate(45); c.drawCentredString(0, 0, "DRAFT")
c.save()
stamp = PdfReader(buf).pages[0]

out = PdfWriter(clone_from=src)
for page in out.pages:
    page.merge_page(stamp)          # over the content; use page.merge_page with a copy under for "behind"
out.write("watermarked.pdf")
```
Page numbers on an existing PDF: same idea - one small overlay per page with
`c.drawRightString(w - 40, 30, f"{i} / {n}")`, merged onto page i.

## Bookmarks, metadata, cropping, compression

```python
w = PdfWriter(clone_from="in.pdf")
w.add_outline_item("Introduction", 0)                     # page index
parent = w.add_outline_item("Results", 4)
w.add_outline_item("Table 2", 6, parent=parent)
w.add_metadata({"/Title": "Annual Report 2026", "/Author": "Finance team"})
p = w.pages[0]; p.mediabox.lower_left = (36, 36); p.mediabox.upper_right = (576, 756)   # crop
for page in w.pages:
    page.compress_content_streams()
w.compress_identical_objects()                          # dedupe identical objects
w.write("out.pdf")
```
Heavier compression (downsampling images): `gs -sDEVICE=pdfwrite -dPDFSETTINGS=/ebook -o small.pdf in.pdf`
if Ghostscript is installed.

## qpdf

```bash
qpdf --empty --pages a.pdf 1-3 b.pdf 5-7 -- combined.pdf    # pick ranges from several files
qpdf in.pdf --pages . 1,3-5,10-z -- subset.pdf                # z = last page
qpdf --split-pages=1 in.pdf page_%d.pdf                       # one file per page
qpdf in.pdf out.pdf --rotate=+90:1,3                           # rotate pages 1 and 3
qpdf --linearize in.pdf web.pdf                               # fast web view
qpdf --check in.pdf ; qpdf in.pdf repaired.pdf               # diagnose / rewrite damaged file
qpdf --encrypt user owner 256 --print=none --modify=none -- in.pdf locked.pdf
qpdf --show-encryption locked.pdf
```

## poppler CLI

```bash
pdfinfo in.pdf                                    # pages, size, producer, encryption
pdftotext -layout -f 1 -l 5 in.pdf out.txt        # layout-preserving text, pages 1-5
pdftotext -bbox-layout in.pdf boxes.html           # words with coordinates
pdfimages -list in.pdf ; pdfimages -all in.pdf img/p   # embedded images, original format
pdftoppm -png -r 150 -f 2 -l 2 in.pdf page          # render page 2
pdfseparate in.pdf p-%d.pdf ; pdfunite a.pdf b.pdf out.pdf
```

## Troubleshooting

- **Encrypted:** `r = PdfReader(p); r.is_encrypted and r.decrypt("password")`. Owner-password-only
  PDFs often open with an empty user password. Don't bypass protection the user isn't
  entitled to remove.
- **Garbled text / `(cid:NN)`:** fonts without a Unicode map - OCR the rendered page.
- **Text order scrambled (columns):** use `pdftotext -layout`, or crop each column with
  `within_bbox` and extract separately.
- **Damaged file:** `qpdf in.pdf repaired.pdf`, or `pypdf.PdfReader(p, strict=False)`.
- **Huge files:** process page by page; write chunks; prefer CLI tools.
- **pypdf `extract_text` is slow or poor on complex pages:** use pdfplumber or pdftotext.

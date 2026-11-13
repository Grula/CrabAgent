---
name: pdf
description: Read, extract (text, tables, images), merge, split, rotate, watermark, encrypt, OCR, fill forms in, and create PDF files, then verify by rendering pages to images. Use whenever the user mentions a .pdf file, wants data pulled out of a PDF, a PDF form filled, or a document produced as PDF.
---

# PDF Processing

Scripts and references are relative to the skill directory shown when this skill loads.
The office tools do not open PDFs - use Python and command-line tools via `bash`.

## Dependencies (install only what the task needs)

| Need | Package | Install |
|---|---|---|
| Read / merge / split / rotate / encrypt / forms | `pypdf` | `pip install pypdf` |
| Text with layout, tables, word coordinates | `pdfplumber` | `pip install pdfplumber` |
| Render pages to PNG (no system deps) | `pypdfium2` | `pip install pypdfium2` |
| Create PDFs | `reportlab` | `pip install reportlab` |
| OCR scanned pages | `pytesseract` + Tesseract binary | `pip install pytesseract` + `apt install tesseract-ocr` / `brew install tesseract` |
| Fast CLI text/images/render | poppler (`pdftotext`, `pdfimages`, `pdftoppm`) | `apt install poppler-utils` / `brew install poppler` |
| Repair, linearize, page surgery | `qpdf` | `apt install qpdf` / `brew install qpdf` |

`pillow` and `lxml` are already available. Check before installing: `python -c "import pypdf"`.

## Quick reference

| Task | Best tool | Notes |
|---|---|---|
| Page count, metadata | `pypdf.PdfReader` | `len(r.pages)`, `r.metadata` |
| Extract plain text | `pdfplumber` / `pdftotext -layout` | empty text => scanned => OCR |
| Extract tables | `pdfplumber page.extract_tables()` | tune settings; verify against the rendered page |
| Merge / split / reorder | `pypdf.PdfWriter` / `qpdf --pages` | |
| Rotate, crop | `page.rotate(90)`, `page.mediabox` | |
| Watermark / stamp | `page.merge_page(stamp_page)` | |
| Encrypt / decrypt | `writer.encrypt(...)` / `reader.decrypt(pw)` | |
| Extract images | `pdfimages -all in.pdf out/img` | |
| Fill a form | `scripts/` workflow in [references/forms.md](references/forms.md) | |
| Create a document | reportlab (Platypus) - [references/creating-pdfs.md](references/creating-pdfs.md) | or build .docx and convert |
| Office file -> PDF | `soffice --headless --convert-to pdf file.docx` | keeps Word/PowerPoint layout |
| Look at pages | `python scripts/convert_pdf_to_images.py in.pdf outdir/` | then `read` the PNGs |

## Reading

```python
import pdfplumber
with pdfplumber.open("in.pdf") as pdf:
    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        print(f"--- page {i} ({len(text)} chars)")
        print(text)
        for t in page.extract_tables():
            print(t)            # list of rows; first row is usually the header
```
- Long documents: extract to a text file and read it in parts; note page numbers so you
  can cite them.
- If pages return little or no text (or `(cid:NN)` garbage), the PDF is scanned or uses
  unmapped fonts: OCR it (below).
- Tables: compare extracted rows with the rendered page. Multi-line cells, merged headers
  and borderless tables often need `table_settings` (see [references/advanced.md](references/advanced.md)) or
  word-coordinate parsing. Numbers come back as strings - clean thousands separators,
  parentheses negatives and footnote markers before analysis.
- Large tables to Excel: build a DataFrame per table and hand off to the xlsx skill
  (or `df.to_excel`).

## Manipulating

```python
from pypdf import PdfReader, PdfWriter

w = PdfWriter()                                       # merge
for path in ["a.pdf", "b.pdf"]:
    w.append(path)
w.write("merged.pdf")

r = PdfReader("in.pdf")                               # split / extract pages 3-5
w = PdfWriter()
for p in r.pages[2:5]:
    w.add_page(p)
w.write("pages3-5.pdf")

w = PdfWriter(clone_from="in.pdf")                    # rotate page 1, encrypt
w.pages[0].rotate(90)
w.encrypt(user_password="open123", owner_password="owner456")
w.write("out.pdf")
```
CLI equivalents: `qpdf --empty --pages a.pdf b.pdf -- merged.pdf`;
`qpdf in.pdf --pages . 1,3-5,8-z -- subset.pdf`; `qpdf --decrypt --password=pw in.pdf out.pdf`.
Watermarks, page numbers on existing PDFs, cropping, bookmarks, compression: [references/advanced.md](references/advanced.md).

## OCR (scanned PDFs)

```python
import pypdfium2 as pdfium, pytesseract
pdf = pdfium.PdfDocument("scan.pdf")
for i, page in enumerate(pdf, 1):
    img = page.render(scale=300 / 72).to_pil()            # 300 dpi for OCR
    print(f"--- page {i}\n" + pytesseract.image_to_string(img, lang="eng"))
```
For a searchable PDF instead of text, `ocrmypdf in.pdf out.pdf` (pip/apt `ocrmypdf`) adds a
text layer. If Tesseract is unavailable and the model can see images, render the pages
and `read` them as a fallback - say that the transcription is visual, not OCR.

## Filling forms

Follow [references/forms.md](references/forms.md) step by step. Short version:
1. `python scripts/extract_form_field_info.py form.pdf fields.json` - tells you whether the
   form has fillable fields and lists them.
2. Fillable: write `values.json`, run `python scripts/fill_fillable_fields.py form.pdf values.json out.pdf`
   (validates ids and values, reopens and verifies; `--flatten` for a non-editable copy).
3. Not fillable: locate entry boxes (`scripts/extract_form_structure.py` or rendered images),
   check them with `scripts/check_bounding_boxes.py`, then place text with
   `scripts/fill_pdf_form_with_annotations.py`.
4. Render the result and look at every filled page.

## Creating PDFs

- Reports and documents: reportlab Platypus (flowing text, tables, images, page templates
  with header/footer and page numbers). Recipes and layout rules: [references/creating-pdfs.md](references/creating-pdfs.md).
- When the user also wants an editable version, or the layout is Word-like, build the
  `.docx` (docx skill) and convert with LibreOffice: `soffice --headless --convert-to pdf report.docx`.
- HTML/CSS designs: render with a headless browser if available
  (`playwright` `page.pdf()`), else convert via LibreOffice or rebuild in reportlab.
- Unicode: reportlab's built-in fonts cover Latin-1 only. Register a TTF (DejaVuSans,
  Noto Sans, a CJK font) for anything else, and never use Unicode sub/superscript digits -
  use `<sub>`/`<super>` markup in Paragraphs.

## Verify before reporting done

1. Render: `python scripts/convert_pdf_to_images.py out.pdf out_pages/` and `read` the PNGs
   (all pages for short documents; first, last and a sample for long ones).
2. Look for clipped or overlapping text, tables running past margins, black boxes or
   missing glyphs (font problem), blank pages, wrong page order or rotation, and leftover
   placeholders.
3. Re-open programmatically: page count as expected, text extractable (`pdfplumber`),
   form values present (`reader.get_fields()`), encryption applied when requested.
4. Keep intermediate files out of the user's folders (use a temp directory) and report the
   final path plus anything you could not verify.

<!-- Adapted from Anthropic claude-code skills/pdf/SKILL.md (Apache-2.0). Modified for CrabAgent. -->

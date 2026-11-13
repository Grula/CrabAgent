# Filling PDF forms

Do these steps in order. Script paths are relative to the skill directory; they need
`pypdf` (and `pdfplumber` for structure extraction, `pypdfium2` or poppler for images).

## Step 0 - Which kind of form?

```bash
python scripts/extract_form_field_info.py form.pdf field_info.json
```
It prints whether the PDF has fillable (AcroForm) fields. Fillable -> section A.
No fields -> section B. (XFA-only forms from some government sites show no AcroForm
fields; treat them as section B or ask for a flattened/AcroForm version.)

Always render the blank form once so you understand what each field means:
```bash
python scripts/convert_pdf_to_images.py form.pdf form_pages/
```
`field_info.json` gives each field's `rect` in PDF points with y=0 at the **bottom**;
an image of height H rendered at `dpi` maps as `x_img = x * dpi/72`,
`y_img = H - y * dpi/72`.

## A. Fillable fields

`field_info.json` entries:
```json
{"field_id": "applicant.last_name", "page": 1, "type": "text", "rect": [x0, y0, x1, y1]}
{"field_id": "Check12", "page": 1, "type": "checkbox", "checked_value": "/Yes", "unchecked_value": "/Off"}
{"field_id": "status", "page": 2, "type": "radio_group", "radio_options": [{"value": "/Married", "rect": [...]}]}
{"field_id": "state", "page": 2, "type": "choice", "choice_options": [{"value": "CA", "text": "California"}]}
```

1. Map every value the user gave you to a field. Field names are often cryptic
   (`Text17`); use the rendered page and the `rect` positions to identify them. Do not
   guess when two fields are plausible - check the image.
2. Write `values.json`:
   ```json
   [{"field_id": "applicant.last_name", "page": 1, "value": "Simpson"},
    {"field_id": "Check12", "page": 1, "value": "/Yes"},
    {"field_id": "status", "page": 2, "value": "/Married"}]
   ```
   Checkboxes take exactly `checked_value`/`unchecked_value`; radio groups and choices take
   one of their listed `value`s. Dates and numbers in the format the form shows.
3. Fill:
   ```bash
   python scripts/fill_fillable_fields.py form.pdf values.json filled.pdf            # stays editable
   python scripts/fill_fillable_fields.py form.pdf values.json filled_flat.pdf --flatten
   ```
   The script rejects unknown ids, wrong pages and invalid option values (fix and rerun),
   then reopens the output and confirms each stored value.
4. Render `filled.pdf` and inspect each page: values visible, not clipped, in the right
   boxes, checkboxes showing. Some viewers draw values only from appearance streams; if a
   value is stored but invisible in the render, produce a flattened copy as well.

Keep the result interactive unless the user wants a final, non-editable copy. Never
flatten a digitally signed PDF without asking - it invalidates the signature.

## B. Non-fillable forms (text placed on top)

### B1. Find coordinates from the PDF structure (preferred)

```bash
python scripts/extract_form_structure.py form.pdf structure.json
```
`structure.json` contains `labels` (each word with `x0, top, x1, bottom` in points, **y=0 at
the top**), long horizontal `lines`, small square `checkboxes`, and `row_boundaries`.
If labels are empty or look like `(cid:12)`, the page is an image - go to B2.

For each field:
- entry box starts a few points right of its label (`label.x1 + 4`) and ends before the
  next label or at the line's end;
- vertically it sits between the row boundaries (or from the label's `top` to the line
  under it);
- checkboxes: use the detected square directly; put an "X" (or "✓" if the font has it).

### B2. Estimate from images (scanned forms)

1. `python scripts/convert_pdf_to_images.py form.pdf pages/ --dpi 150` and note each
   image's pixel size (printed by the script).
2. Read each page image and estimate boxes; then crop around each field to refine:
   ```python
   from PIL import Image
   im = Image.open("pages/page_1.png"); im.crop((50, 120, 450, 220)).save("crop_name.png")
   ```
   Pixel coordinates in the crop + the crop offset = full-image coordinates.
3. Keep image coordinates consistent: all boxes for a page use that page's image size.

### B3. Describe the fields

```json
{
  "pages": [{"page_number": 1, "pdf_width": 612, "pdf_height": 792}],
  "form_fields": [
    {"page_number": 1, "description": "Last name", "field_label": "Last Name",
     "label_bounding_box": [43, 63, 87, 73], "entry_bounding_box": [92, 63, 260, 79],
     "entry_text": {"text": "Smith", "font_size": 10}},
    {"page_number": 1, "description": "US citizen - Yes", "field_label": "Yes",
     "label_bounding_box": [260, 200, 280, 210], "entry_bounding_box": [285, 197, 292, 205],
     "entry_text": {"text": "X", "font_size": 8}}
  ]
}
```
- Use `pdf_width`/`pdf_height` for coordinates from B1 (points, top-left origin) or
  `image_width`/`image_height` for B2 (pixels). One system per page.
- Boxes are `[left, top, right, bottom]`. Optional `font`, `font_color` (hex) in `entry_text`.
- Mixing: convert image coordinates to points with `x * pdf_width / image_width`,
  `y * pdf_height / image_height` and declare `pdf_width/pdf_height`.

### B4. Validate, fill, verify

```bash
python scripts/check_bounding_boxes.py fields.json        # overlaps, boxes too small for the font
python scripts/fill_pdf_form_with_annotations.py form.pdf fields.json filled.pdf
python scripts/convert_pdf_to_images.py filled.pdf check/
```
Optional overlay to debug placement before filling:
```python
from PIL import Image, ImageDraw
import json
data, page = json.load(open("fields.json")), 1
im = Image.open("pages/page_1.png"); d = ImageDraw.Draw(im)
for f in data["form_fields"]:
    if f["page_number"] == page:
        d.rectangle(f["entry_bounding_box"], outline="red", width=2)   # image-coordinate boxes only
        d.rectangle(f["label_bounding_box"], outline="blue", width=2)
im.save("overlay_1.png")
```
If text lands in the wrong place: check that the coordinate system you declared matches
the numbers (points vs pixels, top-left origin), and that image sizes match the images you
measured.

## Finishing

- Re-read the filled form against the user's data, field by field.
- Leave fields blank rather than inventing values; list anything you could not fill and why.
- Signatures: never fabricate a signature. Leave the signature line empty unless the user
  supplies a signature image and asks you to place it.

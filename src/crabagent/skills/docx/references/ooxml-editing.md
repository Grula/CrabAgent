# Editing .docx XML directly

Use this when neither the office tools nor python-docx can express the change, or when
you need exact control over tracked changes and comments. A `.docx` is a ZIP; the main
text lives in `word/document.xml`.

## Helper script

`scripts/docx_xml_tools.py` (needs `lxml`, which CrabAgent already depends on) covers the
common raw-XML jobs without unzipping by hand:

```bash
python scripts/docx_xml_tools.py text in.docx                    # numbered paragraphs
python scripts/docx_xml_tools.py merge-runs in.docx -o merged.docx
python scripts/docx_xml_tools.py track-replace in.docx --find "30 days" --replace "45 days" \
       --author "Reviewer" -o redlined.docx                       # add --all for every match
python scripts/docx_xml_tools.py comment redlined.docx --find "liability cap" \
       --text "Cap looks low for this deal size" --author "Reviewer"
python scripts/docx_xml_tools.py list-changes redlined.docx
python scripts/docx_xml_tools.py accept-all redlined.docx -o clean.docx
```
Paths are relative to this skill's directory. Without `-o` the input is rewritten in place.

## Manual unpack / edit / repack

```bash
mkdir work && cd work && python -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall('x')" ../in.docx
find x -type l -delete              # never follow symlinks from an untrusted package
# ... edit x/word/document.xml (keep it on one line if it was; do not pretty-print) ...
python - <<'EOF'
import os, zipfile
names = []
for root, _, files in os.walk("x"):
    for f in files:
        names.append(os.path.relpath(os.path.join(root, f), "x"))
names.sort(key=lambda n: n != "[Content_Types].xml")          # content types first
with zipfile.ZipFile("../out.docx", "w", zipfile.ZIP_DEFLATED) as z:
    for n in names:
        z.write(os.path.join("x", n), n)                         # files only - no directory entries
EOF
```
Parse with `lxml.etree` (keeps namespace prefixes); avoid `xml.etree.ElementTree`, which
rewrites prefixes and can make Word reject the file. Escape `&`, `<`, `>` in text and keep
`xml:space="preserve"` on any `<w:t>` with leading/trailing spaces.

## Why your search string is "not in the document"

Word splits visible text across many `<w:r>` runs (spell-check markers, revision ids,
edit history), so "Termination for Convenience" may be three runs. Merge identically
formatted adjacent runs first (`merge-runs`), and search per paragraph using the
concatenated text of its runs. A phrase that changes formatting mid-way (one bold word)
will always span runs - edit those runs individually.

## Tracked changes (redlining)

Rules:
- Wrap only the text that changes. Unchanged words stay outside `<w:ins>`/`<w:del>`.
- Every `<w:ins>`/`<w:del>` needs `w:id` (unique in the document), `w:author`, `w:date`
  (ISO 8601, e.g. `2026-11-12T10:00:00Z`).
- Inside `<w:del>` the text element is `<w:delText>`, not `<w:t>`.
- Copy the original run's `<w:rPr>` into both the deleted and inserted runs so formatting
  is preserved.

Replace one word ("monthly" -> "quarterly"):
```xml
<w:r><w:t xml:space="preserve">The fee is </w:t></w:r>
<w:del w:id="101" w:author="Reviewer" w:date="2026-11-12T10:00:00Z">
  <w:r><w:delText>monthly</w:delText></w:r>
</w:del>
<w:ins w:id="102" w:author="Reviewer" w:date="2026-11-12T10:00:00Z">
  <w:r><w:t>quarterly</w:t></w:r>
</w:ins>
<w:r><w:t xml:space="preserve"> and payable in USD.</w:t></w:r>
```

Delete a whole paragraph: wrap every run in `<w:del>` AND mark the paragraph mark deleted,
otherwise an empty paragraph (or empty bullet) is left behind after acceptance:
```xml
<w:p>
  <w:pPr><w:rPr><w:del w:id="103" w:author="Reviewer" w:date="2026-11-12T10:00:00Z"/></w:rPr></w:pPr>
  <w:del w:id="104" w:author="Reviewer" w:date="2026-11-12T10:00:00Z"><w:r><w:delText>Old clause.</w:delText></w:r></w:del>
</w:p>
```
A deleted paragraph mark means "join this paragraph with the next one". Inside `<w:rPr>`
the `<w:del/>` marker must come before the other formatting children (schema order).

Insert a new paragraph: put the runs in `<w:ins>` and add
`<w:pPr><w:rPr><w:ins w:id=".." w:author=".." w:date=".."/></w:rPr></w:pPr>`.

Formatting-only change: inside the run's `<w:rPr>` add
`<w:rPrChange w:id=".." w:author=".." w:date=".."><w:rPr>...old formatting...</w:rPr></w:rPrChange>`.

Turn on change tracking for the user's later edits: add `<w:trackRevisions/>` to
`word/settings.xml`.

Check your work: `list-changes` shows every revision; `office_read` shows the text as it
reads with changes applied. Every intended edit must appear as a revision - an edit made
outside `<w:ins>`/`<w:del>` is invisible in the "original" view and defeats the redline.

Accepting changes: `accept-all` (script), `officecli set f.docx /revision --prop
revision.action=accept`, or LibreOffice. Viewers differ on fully deleted paragraphs; if an
empty bullet remains in a preview, check the XML before assuming the document is wrong.

## Comments

A comment needs: an entry in `word/comments.xml`, a relationship from
`word/_rels/document.xml.rels` (Type `.../relationships/comments`, Target `comments.xml`),
a content-type override for `/word/comments.xml`
(`application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml`), and anchors
in the body:

```xml
<w:commentRangeStart w:id="7"/>
<w:r><w:t>liability cap</w:t></w:r>
<w:commentRangeEnd w:id="7"/>
<w:r><w:commentReference w:id="7"/></w:r>
```
```xml
<!-- word/comments.xml -->
<w:comment w:id="7" w:author="Reviewer" w:initials="R" w:date="2026-11-12T10:00:00Z">
  <w:p><w:r><w:annotationRef/></w:r><w:r><w:t>Cap looks low for this deal size.</w:t></w:r></w:p>
</w:comment>
```
Until the anchors are placed, the comment exists but is not shown. Replies and the
"resolved" flag live in `commentsExtended.xml` (`w15:commentEx` with `w15:paraIdParent`,
`w15:done`); the office tools handle those (`parentId`, `done`) - prefer them for threads.

## Images by hand

Copy the image to `word/media/`, add a relationship (Type `.../relationships/image`), a
`<Default Extension="png" ContentType="image/png"/>` in `[Content_Types].xml` if missing, and
an inline `<w:drawing>`. This is long and error-prone - use `office_edit` (picture) or
python-docx `add_picture` instead whenever possible.

## Validation

- `officecli validate out.docx` (schema) and `officecli view out.docx issues`.
- Re-open with python-docx (`docx.Document("out.docx")`) - it fails fast on broken packages.
- Render with `scripts/render_to_images.py` and look at the pages.

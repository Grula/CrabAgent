---
name: docx
description: Create, read, edit and review Word documents (.docx) - headings/styles, tables, images, headers/footers, TOC, templates, find-and-replace, tracked changes and comments. Use when the user mentions Word, .docx, a Word template, redlining, or wants a report/letter/memo delivered as a Word file.
---

# Word Documents (.docx)

A `.docx` is a ZIP of XML parts (`word/document.xml`, `styles.xml`, `numbering.xml`, headers, footers, `comments.xml`, ...). You have three ways to work on one; pick per task and mix freely. Scripts and references below are relative to the skill directory shown when this skill loads.

| Task | First choice | Fallback |
|---|---|---|
| Read / summarize | `office_read` (mode `text`, `outline`, `stats`) | `python-docx`, `pandoc -t markdown file.docx` |
| Create a new document | `office_create` + `office_batch_edit` | `python-docx` script ([references/python-docx.md](references/python-docx.md)) |
| Targeted edits to an existing file | `office_query` to locate, `office_edit` / `office_batch_edit` to change | `python-docx`, or raw XML ([references/ooxml-editing.md](references/ooxml-editing.md)) |
| Tracked changes / comments | `office_edit` revision + comment props | `scripts/docx_xml_tools.py` (track-replace, comment, accept-all) |
| Fill a `{{placeholder}}` template | `officecli merge` via bash | python-docx run-level replace |
| Legacy `.doc` | convert first: `soffice --headless --convert-to docx file.doc` | - |

The office tools wrap the OfficeCLI binary. Property names and element paths for docx are in [references/officecli-docx.md](references/officecli-docx.md). When a property is rejected, look it up instead of guessing: `office_help(file_format="docx")`, or via bash `officecli help docx <element>` (binary on PATH or at `~/.officecli/officecli` once any office tool has run).

## Ground rules

- **Never overwrite the user's original** unless they asked for an in-place edit. Write to `<name>_edited.docx` (or the name they gave) and say where it is.
- **Read before you edit.** `office_read(mode="outline")` shows the heading tree, tables and sections; `office_read(mode="text")` shows paths like `/body/p[12]` you can address directly.
- **Match the existing document.** Reuse its styles (`style=Heading2`, its body style, its table look) instead of hand-formatting runs. Existing conventions beat every default here.
- **Use real structure, not lookalikes:** headings via heading styles (so TOC and navigation work), lists via list styles / numbering (never a typed `•`), page breaks via `pageBreakBefore` or a break element (never a run of empty paragraphs), horizontal rules via a paragraph bottom border (never a one-row table).
- **Paths shift.** After inserting or removing paragraphs, positional paths (`/body/p[7]`) change. Re-query, or prefer stable ids (`/body/p[@paraId=...]`) returned by `office_query`.
- **One save cycle when possible.** Batch related changes in one `office_batch_edit` call; it is atomic (any failure rolls the batch back).
- **Before another program touches the file** (python-docx, LibreOffice, pandoc), flush OfficeCLI's in-memory copy: `officecli close <file>` via bash (harmless if nothing is open).

## Creating a document with the office tools

1. `office_create(file_path="report.docx")`.
2. Page setup and defaults (US Letter is 12240 x 15840 twips, A4 is 11906 x 16838; 1440 twips = 1 inch):
   `office_edit(command="set", element_path="/", props={"pageWidth":"12240","pageHeight":"15840","marginTop":"1440","marginBottom":"1440","marginLeft":"1440","marginRight":"1440","docDefaults.font":"Calibri","docDefaults.fontSize":"11pt"})`
3. Content in one batch - headings use built-in styles so they reach the TOC:
   ```json
   [{"command":"add","parent":"/body","type":"paragraph","props":{"text":"Quarterly Review","style":"Title"}},
    {"command":"add","parent":"/body","type":"paragraph","props":{"text":"Summary","style":"Heading1"}},
    {"command":"add","parent":"/body","type":"paragraph","props":{"text":"Revenue grew 18% year over year.","spaceAfter":"8pt"}},
    {"command":"add","parent":"/body","type":"paragraph","props":{"text":"Enterprise renewals","listStyle":"bullet"}},
    {"command":"add","parent":"/body","type":"table","props":{"rows":"4","cols":"3","width":"100%"}}]
   ```
4. Fill and style the table: header row `set /body/tbl[1]/tr[1]` with `{"header":"true","c1":"Quarter","c2":"Revenue","c3":"Growth"}`; cell shading `set .../tc[N]` `{"fill":"1F4E79"}`; header text `set .../tc[N]/p[1]/r[1]` `{"bold":"true","color":"FFFFFF"}`; right-align numbers with `{"align":"right"}` on the cell paragraph.
5. Images: `add` type `picture` under the paragraph that should hold it, props `{"src":"/abs/path/chart.png","width":"6in","alt":"Revenue by quarter"}`. Give only width (or only height) to keep the aspect ratio; always set `alt`.
6. Header/footer with page numbers: `add` to `/` type `footer` `{"type":"default","text":"Page ","field":"page","align":"center","size":"9pt"}`.
7. TOC (optional): `add` to `/body` type `toc` `{"levels":"1-3","title":"Contents","hyperlinks":"true"}` at position 0 (`office_edit`: `props.index=0`; `office_batch_edit`: top-level `"index":0`), then `set /settings {"updateFields":"true"}` so Word fills page numbers on open.
8. Verify (see below).

Pass booleans as strings (`"true"`), colors as hex without `#`, sizes with units (`11pt`, `1in`, `2cm`). Tool props are passed straight to the CLI (no shell), so `$` and quotes in text are safe.

## Editing an existing document

1. Orient: `office_read(mode="outline")`, then `office_read(mode="text", offset=..., max_lines=...)` for the region you need.
2. Locate: `office_query(path_or_selector='paragraph:contains("Termination")', mode="selector")`, `'paragraph[style=Heading1]'`, `'table:*'`.
3. Change text or format with `set` on the paragraph/run path. Inserting relative to an anchor: `add` with `props.after` / `props.before` set to the anchor's path.
4. Whole-document find & replace (works across run boundaries) via bash: `officecli set file.docx / --find "Acme Ltd" --replace "Acme GmbH"` - add `--prop revision.author="Reviewer"` to make it a tracked change. Scope it by using a paragraph path instead of `/`.
5. Re-read the changed region to confirm.

If the tools cannot express an edit (complex numbering, content controls, odd template constructs), switch to python-docx or raw XML. Word splits visible text across many `<w:r>` runs, so a phrase you can see may not exist as one string in the XML - see [references/ooxml-editing.md](references/ooxml-editing.md) for the safe procedure.

## Tracked changes and comments

- **Insert/delete as a revision:** on a run, `{"revision.type":"ins","revision.author":"<name>"}` or `"del"`. Tracked find/replace: see step 4 above.
- **Accept / reject:** `set /revision {"revision.action":"accept"}` (all), narrowed by selector, e.g. `/revision[@author=Alice]` or `/revision[@type=del]`. List them with `office_query("revision", mode="selector")`.
- **Comment:** `add` to the paragraph path, type `comment`, `{"author":"<name>","text":"Cap seems low"}`; reply with `"parentId":"<id>"`; resolve with `set /comments/comment[N] {"done":"true"}`.
- Use the author name the user asks for; otherwise a neutral one such as "Reviewer". Never edit text silently in a document the user asked you to redline - every change must be inside a revision.
- Without the office tools: `python <skill_dir>/scripts/docx_xml_tools.py track-replace in.docx --find OLD --replace NEW --author NAME -o out.docx`, plus `comment`, `list-changes`, `accept-all` subcommands (needs only `lxml`). Raw-XML details in [references/ooxml-editing.md](references/ooxml-editing.md).

## Python fallback

`python-docx` is not a CrabAgent dependency; install it on demand: `pip install python-docx`. It cannot open `.doc`, cannot render, and loses formatting if you assign `paragraph.text` on styled paragraphs (set `run.text` instead). Recipes - styles, tables with explicit widths, shading, images, sections, headers/footers with PAGE fields, TOC field, lists - are in [references/python-docx.md](references/python-docx.md).

## Verify before reporting done

1. **Structure:** `office_read(mode="outline")` - heading levels in order (no H1 -> H3 jumps), expected tables/images present.
2. **Text:** `office_read(mode="text")` - typos, leftover `{{...}}`, `TODO`, `lorem`, literal `\n` or `**markdown**`.
3. **Issues/validity (bash, if the binary is reachable):** `officecli view file.docx issues` and `officecli validate file.docx`.
4. **Visual:** `office_render` returns an HTML preview. For a page-accurate look, render to PNG and open the images with `read`:
   `python <skill_dir>/scripts/render_to_images.py report.docx` (LibreOffice -> PDF -> PNG; prints the image paths). Check pagination, blank pages, table widths running off the page, image sizing, header/footer content, and that the TOC is not on the same page as body text.
5. Fix, re-render the affected pages, and only then report - with the output path and anything you could not verify (e.g. TOC page numbers fill in when Word opens the file).

## Writing quality defaults (when the user gives no template)

- Body 10.5-12pt, line spacing about 1.15-1.3, 6-10pt after paragraphs; headings 1.3-2x body size, bold, `keepNext`.
- Margins 1in (2.54cm); tables full text width with a repeated header row and no row split across pages.
- One accent color for headings and table headers; dark text on light fills only.
- Real document furniture where it helps: title block, page numbers, a TOC for documents over ~6 pages, captions under figures and tables.

<!-- Adapted from Anthropic claude-code skills/docx/SKILL.md (Apache-2.0). Modified for CrabAgent. -->

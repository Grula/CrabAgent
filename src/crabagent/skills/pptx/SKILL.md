---
name: pptx
description: Build, edit, read and QA PowerPoint decks (.pptx/.potx) - slides, shapes, text, tables, native charts, images, speaker notes, templates, reordering/duplicating slides - and verify by rendering. Use whenever a .pptx file is the input or output; pair with ppt-designer for visual design choices.
---

# PowerPoint Mechanics (.pptx)

This skill covers **how** to build and change decks reliably. For **what they should look like** (palettes, font pairings, layout patterns, visual motifs) load the `ppt-designer` skill and follow it. Scripts and references are relative to the skill directory shown when this skill loads.

| Task | First choice | Fallback |
|---|---|---|
| Read a deck (incl. notes, tables) | `office_read` (`outline` for slide titles; `text` for content) | `python scripts/pptx_inventory.py deck.pptx` (shapes, positions, fonts, warnings) |
| New deck | `office_create` + `office_batch_edit` | python-pptx ([references/python-pptx.md](references/python-pptx.md)) |
| Change text/format in an existing deck | `office_query` to locate, `office_edit` to set | python-pptx run-level replace |
| Duplicate / reorder / delete slides | `office_edit` move/swap/remove; clone via bash `officecli add deck.pptx / --from '/slide[2]'` | python-pptx XML helpers |
| Fill a template | duplicate template slides, then replace text per shape | python-pptx inventory + replace |
| Look at it | `office_render` (HTML) | `python scripts/render_to_images.py deck.pptx` -> PNGs to `read` |

Element paths and properties: [references/officecli-pptx.md](references/officecli-pptx.md). If a prop is rejected, check `office_help(file_format="pptx")` or bash `officecli help pptx <element>` (`shape`, `slide`, `chart`, `table`, `picture`, `connector`) instead of guessing.

## Ground rules

- **Plan the deck before building.** List each slide's purpose and its single message; pick the visual system once (via `ppt-designer`). One message per slide; the title states the takeaway ("Churn fell 3 points after onboarding fix"), not a topic label ("Churn").
- **Work on a copy** when editing a user's deck; output `<name>_edited.pptx` unless told otherwise.
- **Know the canvas.** Read the slide size first (`office_query("/")` or the inventory script). 16:9 is usually 13.333 x 7.5 in (python-pptx/PowerPoint default "widescreen") or 10 x 5.625 in; 4:3 is 10 x 7.5 in. Every coordinate must sit inside it, with >= 0.5 in margins.
- **Structural changes first, content second.** Add, duplicate, delete and reorder slides before filling text - positional paths (`/slide[4]/shape[2]`) shift after structural edits. Prefer stable ids (`shape[@id=...]` or `shape[@name=...]`) for multi-step work.
- **Native over pictures.** Use real text boxes, tables and charts (editable, crisp, accessible). Only diagrams PowerPoint cannot draw (Sankey, network graphs) go in as images.
- **Speaker notes** go in the notes element, never in a text box on the slide.
- **Text must fit its box.** If content does not fit: cut words, enlarge the box, or split the slide. Do not shrink body text below ~14 pt (captions ~10 pt) in decks you create; in an existing deck match its sizes.
- **Before another program touches the file**, flush OfficeCLI's resident copy: bash `officecli close deck.pptx` (harmless if nothing is open).

## Building with the office tools

```json
[{"command":"add","parent":"/","type":"slide","props":{"layout":"blank","background":"1E2761"}},
 {"command":"add","parent":"/slide[1]","type":"shape","props":{"name":"Title","text":"FY26 Strategic Review","x":"0.8in","y":"2.6in","width":"11.7in","height":"1.2in","font":"Georgia","size":"40","bold":"true","color":"FFFFFF"}},
 {"command":"add","parent":"/slide[1]","type":"shape","props":{"name":"Subtitle","text":"Board meeting - 14 Nov 2026","x":"0.8in","y":"3.9in","width":"11.7in","height":"0.6in","size":"18","color":"CADCFC"}},
 {"command":"add","parent":"/","type":"slide","props":{"layout":"blank","background":"FFFFFF"}},
 {"command":"add","parent":"/slide[2]","type":"chart","props":{"chartType":"column","title":"Revenue by quarter ($M)","categories":"Q1,Q2,Q3,Q4","data":"FY25:3.1,3.4,3.6,3.9;FY26:3.8,4.1,4.4,4.9","x":"0.8in","y":"1.6in","width":"7.5in","height":"5in","colors":"9AA5B1,1F5FAD","legend":"bottom"}},
 {"command":"add","parent":"/slide[2]","type":"notes","props":{"text":"Lead with the Q4 beat; EMEA drove half of it."}}]
```
- Build one or two slides, render, check, then continue - mistakes repeat across a deck.
- Use units explicitly (`in`, `cm`, `pt`). Colors hex without `#`. Booleans `"true"`.
- `layout=blank` gives full control; title/content layouts give placeholders (`shape[1]` is usually the title placeholder) - fill those rather than stacking new text boxes on top of them.
- Multi-paragraph text: add `paragraph` elements under the shape (`level` for indent), or `run`s under a paragraph for mixed formatting (bold label + normal text). Bullets: `set` the shape `{"list":"bullet"}` (or `numbered`) - never a typed `•`.
- Tables: `add` type `table` with `data` (2-D array; commas inside cells break the CSV conversion - set such cells afterwards with `set .../tr[r]/tc[c] {"text":...}`), then style header row fill, font size, column widths.
- Images: `add` type `picture` `{"src":"/abs/path.png","x":..,"y":..,"width":..}` plus `alt`. For generated art, create it first with `image_generate` and insert the saved file.
- Transitions/animations exist (`transition`, `animation` props) - use sparingly and only when asked.

## Editing an existing deck

1. `office_read(mode="outline")` and the inventory script to map slides, layouts, shapes and fonts.
2. Find targets: `office_query('shape:contains("Q3")', mode="selector")`, or `office_query("/slide[4]", depth=2)`.
3. Change text with `set` on the shape/paragraph/run path; the run keeps its formatting. Deck-wide replace via bash: `officecli set deck.pptx / --find "FY25" --replace "FY26"` - review every hit, short tokens collide ("18" in "2018").
4. Template slots != your content: if a template has 4 cards and you have 3, remove the 4th card's whole group (shape + text + icon), don't leave it empty.
5. Replacement text longer than the original wraps or overflows. Budget it: keep roughly the original character count for each box (labels/badges stay short phrases); otherwise widen the box if there is free space, or split content across slides. Autofit flags (shrink on overflow) only take effect when PowerPoint opens the file - they cannot be verified by rendering here, so don't rely on them.

python-pptx equivalents (inventory, formatting-preserving replace, slide duplication/deletion/reorder, notes, charts) are in [references/python-pptx.md](references/python-pptx.md). Legacy `.ppt`: convert first with `soffice --headless --convert-to pptx file.ppt`. `.potx` templates: copy to `.pptx` to work on them.

## QA - required before reporting done

Your first render almost always has defects. Treat QA as a bug hunt.

1. **Content:** `office_read(mode="text")` - all planned content present, order right, no typos, no leftovers (`lorem`, `xxxx`, `Click to add`, `TODO`, `{{...}}`, `[Insert`).
2. **Geometry:** `python scripts/pptx_inventory.py deck.pptx` flags off-slide shapes, overlapping text boxes, text too close to edges, empty placeholders, pictures without alt text, tiny fonts and boxes likely too small for their text. Fix every warning or confirm it is intentional.
3. **Issues (bash, if the binary is reachable):** `officecli view deck.pptx issues` and `officecli validate deck.pptx`.
4. **Visual:** render and look at every slide.
   - Quick: `office_render` (HTML preview).
   - Accurate: `python scripts/render_to_images.py deck.pptx` (LibreOffice -> PDF -> PNG, prints paths) and `read` each PNG; or `officecli view deck.pptx screenshot --page N -o slideN.png`.
   - Check: text cut off or overflowing, overlaps, uneven gaps and misaligned columns, margins < 0.5 in, low contrast, a figure split from its unit ("26" / "M" on two lines), titles jumping position between slides of the same type, charts with unreadable labels, leftover placeholder graphics.
5. Fix, re-render the changed slides, repeat until a pass finds nothing. Then report the path, slide count, and anything not verified (e.g. fonts that the renderer substituted).

<!-- Adapted from Anthropic claude-code skills/pptx/SKILL.md (Apache-2.0). Modified for CrabAgent. -->

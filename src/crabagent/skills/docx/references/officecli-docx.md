# OfficeCLI cheat sheet - Word (.docx)

The `office_*` tools run the OfficeCLI binary. This page lists the paths and
properties you will need most. It is a starting point, not the full schema:
the installed CLI version is authoritative. Look things up with
`office_help(file_format="docx")` or, via bash, `officecli help docx <element>`
(e.g. `paragraph`, `run`, `table`, `picture`, `section`, `toc`, `comment`) and
`officecli help docx set paragraph` for the props a verb accepts.

## Tool mapping

| Tool | CLI | Notes |
|---|---|---|
| `office_create(path)` | `create` | refuses to overwrite an existing file |
| `office_read(mode=text/outline/stats)` | `view` | `offset`/`max_lines` page through long docs |
| `office_query(path)` / `(selector, mode="selector")` | `get --json` / `query --json` | `depth` expands children |
| `office_edit(command, element_path, props, element_type)` | `set` / `add` / `remove` / `move` / `swap` | `add`: element_path is the PARENT; position via `props.after/before/index` |
| `office_batch_edit(commands)` | `batch` | atomic; `add` uses `parent` + `type`; `after`/`before`/`index` at command level |
| `office_render` | `view html` | HTML preview (not page-accurate) |

Bash-only commands worth knowing: `officecli view f.docx issues` (empty paragraphs,
missing alt text, formatting anomalies), `officecli view f.docx annotated` (text with
style/font per run), `officecli validate f.docx` (schema check),
`officecli merge template.docx out.docx --data data.json` (fill `{{key}}` placeholders
in body, tables, headers, footers), `officecli dump f.docx /body/tbl[1] -o t.json`
(replayable batch JSON of any subtree), `officecli refresh f.docx` (recompute TOC/PAGE
fields where supported), `officecli close f.docx` (flush before other tools read it).
Quote paths containing brackets in bash: `'/body/p[3]'`.

## Paths (1-based)

```
/                         document root (page setup, docDefaults, metadata)
/body                     body container - parent for add paragraph/table/toc
/body/p[3]                3rd paragraph          /body/p[3]/r[2]   2nd run in it
/body/p[@paraId=1A2B3C4D] stable paragraph id (survives inserts/deletes)
/body/tbl[1]/tr[2]/tc[3]  table 1, row 2, cell 3 /body/tbl[1]/tr[2]/tc[3]/p[1]
/header[1] /footer[1]     header / footer parts
/styles /numbering /settings /comments/comment[N] /revision
```

## Selectors (office_query mode="selector")

`paragraph:contains("Payment")`, `paragraph[style=Heading1]`, `run[font!=Arial]`,
`paragraph[style=Normal] > run[bold=true]`, `table:*`, `picture:no-alt`,
`revision`, `revision[revision.author=Alice]`, `comment[done=false]`, `:empty`.

## Common properties

Paragraph (`add` type `paragraph`, or `set` on `/body/p[N]`):
`text`, `style` (Title, Heading1..Heading9, Normal, ListParagraph, Quote, or a custom
style id), `align` (left/center/right/justify), `size`, `font`, `bold`, `italic`,
`underline`, `color`, `spaceBefore`, `spaceAfter`, `lineSpacing` (`1.15x`, `14pt`),
`indent`, `firstLineIndent`, `keepNext`, `pageBreakBefore`, `listStyle` (bullet / numbered),
`numId` + `ilvl` (custom numbering), `pbdr.bottom` (`single;6;2E75B6` = style;size;color),
`shading.fill`.

Run (`/body/p[N]/r[M]`): `text`, `bold`, `italic`, `underline`, `strike`, `size`, `font`,
`color`, `highlight`, `superscript`/`subscript` (check help), `revision.type`
(ins/del/format), `revision.author`, `revision.date`.

Text-span formatting without addressing runs: `set /body/p[N]` with `find` (+`regex=true`)
plus format props - the CLI splits runs for you.

Table (`add` type `table` under `/body`): `rows`, `cols`, `width` (`100%` or twips),
`style`; or `data` as a 2-D array (cells must not contain commas - they become column
separators). Row: `header=true` (repeat on each page), `c1..cN` cell-text shorthand,
`height`, `cantSplit`. Cell (`tc`): `text`, `fill` / `shd`, `valign`, `colspan` / `gridspan`,
`vmerge` (restart/continue), `width`, `nowrap`, `padding.*`. Cell text formatting goes on
`.../tc[N]/p[1]` or `.../tc[N]/p[1]/r[1]`.

Picture (`add` type `picture` - the tool also accepts `image` - under a paragraph):
`src` (absolute path), `width` or `height` (give one to keep aspect), `alt`,
`decorative`, floating: `anchor`, `wrap`, `hAlign`, `vAlign`, `behindText`.

Page / section (`set /`): `pageWidth`, `pageHeight`, `orientation` (portrait/landscape),
`marginTop/Bottom/Left/Right/Header/Footer` (twips or `2.54cm`), `columns`,
`columnSpace`; metadata `title`, `author`, `subject`; defaults `docDefaults.font`,
`docDefaults.fontSize`.

Header/footer (`add` to `/` type `header` / `footer`): `type` (default/first/even), `text`,
`align`, `size`, `field=page`. Add more fields into the footer paragraph with
`add` type `field` `{"fieldType":"numpages"}` (also `page`, `date`, `mergefield` + `name`).

Other elements: `toc` (`levels`, `title`, `hyperlinks`), `hyperlink` (`url` or `anchor`,
`text`), `bookmark`, `footnote` / `endnote` (`text`), `equation` (`formula` in LaTeX),
`break` (page/column), `watermark`, `comment` (`author`, `text`, `parentId`), `chart`
(`chartType`, `title`, `categories`, `data`), `style` (`id`, `type`, `outlineLvl`, ...).

Field caches: after adding a TOC or PAGE/NUMPAGES fields, `set /settings {"updateFields":"true"}`
so Word refreshes them when the file opens.

## Values

Colors: `1F4E79` (preferred), `#1F4E79`, `red`, `accent1`. Lengths: `1in`, `2.54cm`, `72pt`,
twips as bare numbers for page geometry. Font sizes: `11` or `11pt`. Booleans: `"true"`/`"false"`.

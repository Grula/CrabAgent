---
name: html-email
description: Build HTML emails that render in Gmail, Outlook, Apple Mail and on phones: table layout, inline CSS, 600px column, bulletproof buttons, dark-mode-safe colors, preheader, pre-send checks. Use for newsletters, transactional emails or email templates.
---

# HTML Email

Email HTML is not web HTML. Each client runs its own sanitizer and renderer — Outlook on Windows
uses Word's layout engine, Gmail rewrites or drops `<style>`, many clients block images by default
— so the safe subset is small and old-fashioned. When a web habit conflicts with the rules below,
the email rule wins.

## Deliverable

One self-contained `.html` file (UTF-8) unless the user's tool needs something else (an MJML
source, a template with merge tags such as `{{first_name}}` or `*|FNAME|*`). Start from
`assets/email-template.html`: it already has the document head, Outlook fixes, preheader,
container, bulletproof button, stacking two-column row and footer. Replace every `{{…}}`.

## Structure

- Layout with nested `<table role="presentation" cellpadding="0" cellspacing="0" border="0">`.
  No flexbox, grid, floats or positioning. `role="presentation"` stops screen readers from
  announcing layout tables as data.
- One centered content column, 600 px wide (`width="600"` attribute **and** `style="width:600px;
  max-width:600px"`), inside a full-width background table. Wrap it in an Outlook-only fixed-width
  table (`<!--[if mso]>…<![endif]-->`), because Outlook ignores `max-width`.
- Prefer a single column; it reads best on phones. Side-by-side columns must stack under ~620 px
  via a media query, and must still look acceptable if the media query is stripped.
- Give every table and cell explicit widths in pixels or percentages. Put padding on `<td>`, not
  on `<table>`, `<div>` or `<p>` — Outlook ignores most padding/margin elsewhere.
- Spacing between blocks: padding on cells, or an empty spacer row with `font-size:0;
  line-height:0` and a fixed `height`.

## Styling

- Inline every style that matters, on the element it affects. A `<style>` block in `<head>` is
  progressive enhancement only (media queries, dark-mode overrides, link resets); some clients and
  forwarding paths remove it.
- If you prefer to author with classes, inline afterwards: `npx juice in.html out.html` or
  Python `premailer` — then review the output.
- Fonts: system stacks (`Arial, Helvetica, sans-serif`; `Georgia, 'Times New Roman', serif`;
  `Verdana`; `'Courier New', monospace`). Web fonts work only in a few clients; if used, keep a
  safe fallback stack and an `mso` override so Outlook doesn't fall back to Times.
- Set `line-height` in px with `mso-line-height-rule:exactly`. Use 6-digit hex colors; avoid
  `rgba`, CSS variables, `calc()`, shorthand `background` with images.
- Body text 16 px (14 px minimum), tap targets ~44 px tall.

## Images

- Must be absolute `https://` URLs on a host that will stay up (or `cid:` attachments). Local paths
  and project files won't exist for recipients. If no hosted image exists yet, leave a clearly
  labeled placeholder and tell the user to replace it.
- Always set `alt`, `width` (attribute, in px), `style="display:block; border:0; height:auto;
  max-width:100%"`. Design so the email still makes sense with images blocked — never put the
  headline, offer or call-to-action only inside an image.
- Retina: export at 2× and constrain with `width`.

## Buttons and links

- "Bulletproof" button: a `<td>` with `bgcolor` and background color containing an `<a>` with
  inline padding, color and `display:inline-block`. Add the VML `v:roundrect` version inside an
  `mso` conditional if rounded corners in Outlook matter (template shows both).
- Never use `<button>`, forms, or JavaScript — they are stripped. Every link needs a real `https`
  URL (or merge tag); no `#` placeholders left in a send-ready file.

## Dark mode

Clients either leave colors alone, partially invert light backgrounds (Gmail apps, Outlook), or
honor `prefers-color-scheme` (Apple Mail, some others).

- Include `<meta name="color-scheme" content="light dark">` and `supported-color-schemes`.
- Provide `@media (prefers-color-scheme: dark)` overrides via classes with `!important`; the
  template also includes `[data-ogsc]`/`[data-ogsb]` selectors for Outlook.com.
- Avoid pure `#ffffff`/`#000000` as large backgrounds; slightly off values invert more gracefully.
- Logos with dark text on transparent PNGs vanish on dark backgrounds — add a light outline/padding
  or supply a dark-mode variant.
- Check contrast of brand colors on both light and dark mid-tone fills.

## Inbox details

- First thing in `<body>`: a hidden preheader (~85–100 characters) that complements the subject
  line, followed by invisible padding characters so the client doesn't pull body text into the
  preview.
- `<html lang="…">`, a `<title>` matching the subject, `dir="rtl"` where relevant.
- Keep total HTML under ~100 KB; Gmail clips larger messages behind "View entire message", which
  can also hide the unsubscribe link and tracking.
- Marketing or bulk mail: footer with sender's postal address, why they're receiving it, and a
  working unsubscribe link (legal requirement in many jurisdictions). Transactional mail can omit
  marketing content and unsubscribe but should still identify the sender.
- Also produce a plain-text version if the user's sending tool asks for one.

## Check before handing off

1. Static check:
   ```bash
   python <skill-dir>/scripts/email_check.py email.html --shots email-preview
   ```
   Fix every ERROR; review warnings. `--shots` renders 700 px and 375 px in light and dark.
2. Look at the screenshots (or open `file:///abs/path/email.html` with `browser_navigate`).
   Check the stacked phone layout, button size, and that nothing relies on images.
3. Say clearly that a browser preview approximates Apple Mail at best. Recommend a real send test:
   to the user's own Gmail, Outlook and phone accounts via their sending tool, or a rendering
   service such as Litmus or Email on Acid for broad coverage.

In the reply, give the file path, list remaining placeholders (image URLs, unsubscribe link,
address), and note any client-specific compromises you made.

---
name: frontend-design
description: Design and build distinctive, polished web UI (HTML/CSS, React, landing pages, dashboards, components) with a deliberate aesthetic, type and color tokens, dark mode and phone layouts, verified in a browser. Use when a UI should look good, or for "redesign", "style this", "make it prettier".
---

# Frontend Design

Treat every UI as a design job, not just markup. The aim is something that looks chosen for this
product and audience, works at every width and in both themes, and holds up when inspected.

## Order of authority

1. What the user explicitly asked for (including a look they name).
2. The project's existing design system: look for tokens/theme files, Tailwind config, CSS
   variables, a component library, `AGENTS.md`, brand assets. Use `glob`/`grep` before inventing.
3. Your own choices, below — they fill gaps and never override 1 or 2.

## 1. Read the request and pick a treatment

- **Utilitarian** (internal tool, settings page, docs, dashboard, form): clean hierarchy, careful
  spacing, a restrained palette. No hero, few flourishes. Information design beats decoration.
- **Expressive** (landing page, portfolio, launch page, game, event): commit to a point of view and
  take one real aesthetic risk.

When unsure, a well-composed, quiet page is always acceptable; an over-designed one often isn't.

## 2. Ground it in the subject

Write down (to yourself) one sentence each: who uses this, what the page's single job is, and what
world the subject lives in. Pull at least one concrete detail from that world into the content —
real units, real terminology, the shape of real data — so the page could only belong to this
subject. Use realistic content, never lorem ipsum.

Then name the direction in a few words, e.g. "field-guide: ink, paper, botanical labels",
"control room: dense, monospace numerals, signal colors", "quiet editorial: big serif, wide
margins". Every later choice should be checkable against that phrase.

## 3. Build a small token system first

Put it at the top of the stylesheet as CSS custom properties and derive everything from it:

- **Color**: 4–6 named tokens (background, surface, text, muted text, accent, border) plus
  semantic success/warning/danger for UIs. Tint neutrals slightly toward the accent instead of
  using pure mid-grey. One dominant accent used sparingly outperforms several competing ones.
- **Type**: a display face, a body face, and optionally a mono/utility face. Choose for the
  subject, not by habit. Set a scale (e.g. 12/14/16/20/28/40) and stick to it; vary weight and
  size to build hierarchy. Body text ~16 px, line length ~60–75 characters, headings with
  `text-wrap: balance`, `font-variant-numeric: tabular-nums` where numbers align.
- **Space and shape**: a spacing scale (4/8/12/16/24/32/48/64), one or two radii used by role,
  shadows only where something genuinely floats.

Always give fonts a real fallback stack. If the page must work offline, use system stacks
(`ui-serif`, `ui-sans-serif`, `system-ui`, `ui-monospace`) and get character from scale and weight.

## 4. Both themes, by token

Define every color on `:root`, then redefine only the tokens for dark mode:

```css
:root { --bg:#f7f6f3; --surface:#fff; --text:#1d1c1a; --muted:#5f5b55; --accent:#0b6e4f; --line:#e3e0da; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#141413; --surface:#1d1d1b; --text:#ecebe7; --muted:#a19d96; --accent:#4fc59a; --line:#2e2d2a; color-scheme: dark; } }
:root[data-theme="dark"] { /* same dark values, so an explicit toggle wins */ }
body { background: var(--bg); color: var(--text); }
```

Components use tokens only — never a literal color that is readable in one theme. Re-tune the
accent for dark backgrounds rather than inverting blindly. A deliberately single-theme design is
fine if it is a choice and every color is still set explicitly.

## 5. Layout that survives a phone

- Lay out groups with flex/grid and `gap`, not ad-hoc margins.
- Side gutter at least 16 px at every width; content readable at 360–400 px wide.
- Rows wrap or stack on narrow screens; give text-holding flex/grid children `min-width: 0`.
- `max-width: 100%` on images and media; only tables, code and diagrams may scroll sideways,
  inside their own `overflow-x: auto` wrapper. The page body must never scroll horizontally.
- Size a hero to its content, not `100vh`, so the first screen shows more than a slogan.
- Repeated items (cards, rows, badges) share the same padding, alignment and baseline.
- Borders, fills and shadows mark something as a separate object — use them by role, not on every
  box.

## 6. Motion with restraint

One purposeful moment (a page-load sequence, a satisfying state change) beats animation on
everything. Animate `transform` and `opacity`, keep durations ~150–300 ms for UI feedback, never
hide content behind a scroll-triggered fade, and honor `prefers-reduced-motion: reduce`.

## 7. Craft details

- Interactive things look interactive; every control has hover, active, disabled and a visible
  `:focus-visible` state.
- Semantic HTML (`header`, `nav`, `main`, `button`, `label`), alt text, sufficient contrast
  (4.5:1 body text, 3:1 large text and UI parts).
- Copy is part of the design: name things the way users would, use verbs on buttons ("Save
  changes"), make errors say what happened and how to fix it.
- Structure should encode something true: numbered steps only when order matters, eyebrows only
  when they label real categories.
- For icons, use an inline SVG set or the project's icon library — not emoji.
- Need imagery? Use real assets from the project, or `image_generate` for a bespoke illustration
  or texture; otherwise a tasteful placeholder beats a bad fake.

Before building, scan `references/anti-patterns.md` and make sure the plan doesn't lean on the
defaults that make pages look machine-made.

## 8. Verify in a browser

1. Open it: if you have `browser_navigate`, go to the dev server URL, or to `file:///abs/path/page.html` for a
   static file. Check the screenshot against the direction phrase.
2. The built-in browser is fixed at 1280×800 in light mode, so check other widths and dark mode
   with the bundled script:
   ```bash
   python <skill-dir>/scripts/shoot.py <url-or-file> --out design-shots
   ```
   It saves 1440 px and 390 px shots in light and dark, and reports horizontal overflow, the
   elements causing it, failed fonts and console errors. Look at the screenshots (`read` the PNGs
   if the browser result didn't show them).
3. Fix what you see — clipped text, overflow, unreadable dark-mode text, misaligned rows — and
   re-shoot once. Don't loop endlessly on polish; offer next refinements instead.

In the reply, describe the direction in a sentence or two and list any assumptions (fonts, sample
data, placeholders) the user may want to change.

<!-- references/anti-patterns.md partly adapted from Meta muse-code/skills/taste (Apache-2.0). Modified for CrabAgent. -->

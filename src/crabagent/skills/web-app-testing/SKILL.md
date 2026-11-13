---
name: web-app-testing
description: Test a local or deployed web app like a user: start the dev server, drive flows in the browser, screenshot, collect console/network errors, check accessibility and phone width, report bugs with repro steps. Use for "test this page", "QA my app", "reproduce this UI bug".
---

# Web App Testing

Exercise the app through its UI, gather evidence for every claim, and hand back a report someone
can act on. Testing and fixing are separate phases: don't edit the code under test while testing
unless the user asked for fixes.

## What each tool can and cannot do

| Need | Use |
|---|---|
| Navigate, click, type, scroll, read text, screenshot | `browser_navigate`, `browser_click_index`, `browser_click`, `browser_type`, `browser_scroll`, `browser_extract`, `browser_screenshot` (headless Chromium, fixed 1280×800) |
| A page that needs the user's login, or the user wants to watch | `collab_browser_*` (shared visible browser; `collab_browser_wait_for_user` for logins/CAPTCHAs; local URLs need `collab_browser_authorize_local_preview` first) |
| Console errors, uncaught exceptions, failed/4xx/5xx requests, phone viewport, dark mode, Tab order, a11y scan | `bash` + `scripts/page_probe.py` (the browser tools expose none of these) |
| Multi-step flows you need to repeat exactly, file uploads, arbitrary keys | `bash` + a short Playwright script (see `references/playwright-recipes.md`) |

Limits to keep in mind: `browser_*` cannot resize the viewport, read the console, run page
JavaScript, upload files, or press keys other than Enter (via `browser_type` with `submit`).
If the model cannot view images, screenshots arrive as file paths only — rely on
`browser_extract` text and the probe's JSON, and say so in the report.

## Phase 1 — Plan

- **Steps and expected results given** → run them as written.
- **Feature or bug described** → write down the goal and what "pass" means, then start.
- **Only a URL / "test it"** → open the page, screenshot it, list its main functions, and build a
  short prioritized plan: P0 core flow · P1 feedback (loading, success/error messages, disabled
  states) · P2 input edges (empty, very long, special characters, double submit) · P3 layout and
  visual polish. Show the plan and start on P0 without waiting.

Stop and ask before anything that writes real data or costs money (orders, payments, deletes,
emails to real people) and before entering credentials.

## Phase 2 — Prepare the environment

1. Find the start command (`package.json` scripts, `Makefile`, `pyproject.toml`, README). Prefer an
   already running server: `curl -sI http://localhost:<port>` first.
2. Start it with `bash` and `background: true` (for example `npm run dev`) so it doesn't
   block, and note the PID it returns. Poll with `curl` until it answers; if it doesn't,
   read the server's output with `bash_output` and that PID.
3. Seed test data or accounts if needed — but never pre-create the very thing you are testing
   (don't insert the order when testing "place order").
4. Record every setup action for the report, then state that formal testing starts now. From here
   on, interact only through the UI; if the environment breaks, mark the test point invalid, fix the
   setup, and rerun that point from the start.

## Phase 3 — Execute: act, then observe

1. Open the entry page with `browser_navigate` (use `wait_until: "networkidle"` for SPAs).
2. Run the probe once per important page for the hidden signals:
   `python <skill-dir>/scripts/page_probe.py <url> --out web-test-artifacts`
   Add `--mobile` for a phone viewport, `--dark` for dark mode, `--tab 15` to check keyboard focus
   order, `--axe` for an axe-core scan (needs network).
3. For each test step, act on elements from the numbered list the last browser result returned
   (`browser_click_index` is more reliable than guessing selectors), then check the new state with
   both text (`browser_extract`) and a screenshot. Look at: element present, content correct, state
   changed as expected (URL, messages, enabled/disabled), no overlap, truncation or misalignment.
4. When an element can't be found, don't retry blindly: re-observe, then decide whether it is a
   page bug (record it, move on) or a locator problem (rebuild from what you now see).
5. Don't bypass a broken UI (typing URLs to skip a step, reloading to escape an error state). A
   blocked path is a finding. Reset to the entry page only between test points.
6. Transient states (toasts, spinners) may vanish between tool calls; capture them with a scripted
   Playwright step that acts and screenshots in one run.
7. Mark anything the tooling can't do (drag-and-drop, uploads, native dialogs) as "not testable
   with available tools" rather than guessing.

## Accessibility and responsive basics

Quick pass on every main page (the probe reports most of these):

- `<html lang>`, a meaningful `<title>`, viewport meta present.
- One `h1`, no skipped heading levels; `main`/`nav` landmarks.
- Every image has `alt` (empty `alt=""` for decoration); every input has a label; icon-only buttons
  have an accessible name.
- Tab order follows the visual order, focus is visible on each stop, and modals keep focus inside.
- Tap targets at least 24×24 px (44×44 preferred on touch).
- No horizontal scrolling at 390 px wide; text readable, nothing clipped.
- Text contrast looks sufficient in screenshots (axe reports exact ratios with `--axe`).

## Phase 4 — Report

Default format (follow the user's format if they gave one):

```markdown
## Test report: <app / feature>
Environment: <URL, how it was started, seeded data, browser + viewport>

| # | Test point | Result | Evidence |
|---|---|---|---|
| P0-1 | Sign up with valid email | Pass | web-test-artifacts/p0-1.png |
| P0-2 | Checkout | **Fail** | web-test-artifacts/p0-2.png |

### Bugs
**B1 — <short title>** (severity: blocker/major/minor)
Steps: 1. … 2. … 3. …
Expected: … Actual: …
Evidence: <screenshot path>, console: `TypeError: … at cart.js:42`

### Console and network errors
<from the probe, with the page/step where each occurred>

### Not tested
<blocked or unsupported points and why>
```

Keep screenshots in `web-test-artifacts/` (or the user's chosen folder) and reference real paths
only. Every pass or fail should point at evidence you actually looked at.

## Cleanup

Stop servers you started (`bash_stop` with their PID), close the browser with
`browser_close`, and leave servers the user was already running alone.

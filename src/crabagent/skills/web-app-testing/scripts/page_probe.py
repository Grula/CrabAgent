#!/usr/bin/env python3
"""Load a page in headless Chromium and report what the browser_* tools cannot see.

Collects console errors/warnings, uncaught exceptions, failed requests and HTTP >= 400
responses, takes a screenshot at the requested viewport, and runs read-only accessibility
and layout checks. Optionally walks the keyboard Tab order and runs axe-core.

Usage:
  python page_probe.py URL [--width 1280 --height 800] [--mobile] [--dark]
                           [--out DIR] [--wait load|domcontentloaded|networkidle]
                           [--tab N] [--axe] [--full-page]

Prints one JSON object to stdout. Requires: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("playwright is not installed: pip install playwright && playwright install chromium")

AXE_URL = "https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js"

CHECKS_JS = r"""
() => {
  const out = {};
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const snip = el => el.outerHTML.slice(0, 160);
  const name = el => (el.getAttribute('aria-label') || el.getAttribute('title') ||
    (el.getAttribute('aria-labelledby') || '').split(' ').map(id => document.getElementById(id)?.textContent || '').join(' ') ||
    el.innerText || el.value || el.querySelector('img[alt]')?.alt || '').trim();
  out.document = {
    lang: document.documentElement.lang || null,
    title: document.title || null,
    viewport_meta: document.querySelector('meta[name="viewport"]')?.content || null,
  };
  out.horizontal_overflow_px = Math.max(0, document.documentElement.scrollWidth - window.innerWidth);
  out.images_missing_alt = [...document.images].filter(i => !i.hasAttribute('alt') && vis(i)).map(snip).slice(0, 20);
  out.unlabeled_inputs = [...document.querySelectorAll('input:not([type=hidden]), select, textarea')]
    .filter(i => vis(i) && !(i.id && document.querySelector(`label[for="${CSS.escape(i.id)}"]`)) &&
      !i.closest('label') && !i.getAttribute('aria-label') && !i.getAttribute('aria-labelledby'))
    .map(snip).slice(0, 20);
  out.nameless_controls = [...document.querySelectorAll('button, a[href], [role=button], [role=link]')]
    .filter(el => vis(el) && !name(el)).map(snip).slice(0, 20);
  const heads = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(vis).map(h => +h.tagName[1]);
  out.headings = { h1_count: heads.filter(l => l === 1).length, sequence: heads.slice(0, 40),
    skipped_levels: heads.some((l, i) => i > 0 && l - heads[i - 1] > 1) };
  out.small_tap_targets = [...document.querySelectorAll('button, a[href], input, select, textarea, [role=button]')]
    .filter(vis).map(el => ({ el, r: el.getBoundingClientRect() }))
    .filter(({ r }) => r.width < 24 || r.height < 24)
    .map(({ el, r }) => ({ size: `${Math.round(r.width)}x${Math.round(r.height)}`, html: snip(el) })).slice(0, 20);
  const ids = {}; document.querySelectorAll('[id]').forEach(e => ids[e.id] = (ids[e.id] || 0) + 1);
  out.duplicate_ids = Object.keys(ids).filter(k => ids[k] > 1).slice(0, 20);
  out.landmarks = { main: !!document.querySelector('main,[role=main]'), nav: !!document.querySelector('nav,[role=navigation]') };
  return out;
}
"""

FOCUS_JS = r"""
() => { const el = document.activeElement; if (!el || el === document.body) return null;
  const s = getComputedStyle(el); const r = el.getBoundingClientRect();
  return { tag: el.tagName.toLowerCase(), text: (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 60),
    id: el.id || null, outline: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0, box_shadow: s.boxShadow !== 'none',
    on_screen: r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth }; }
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--mobile", action="store_true", help="390x844 touch viewport, mobile UA")
    ap.add_argument("--dark", action="store_true", help="emulate prefers-color-scheme: dark")
    ap.add_argument("--out", default="web-test-artifacts")
    ap.add_argument("--wait", default="load", choices=["load", "domcontentloaded", "networkidle"])
    ap.add_argument("--settle-ms", type=int, default=800, help="extra wait after load for late errors")
    ap.add_argument("--tab", type=int, default=0, help="press Tab N times and record focus order")
    ap.add_argument("--axe", action="store_true", help="inject axe-core from a CDN (needs network)")
    ap.add_argument("--full-page", action="store_true")
    a = ap.parse_args()

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"url": a.url, "console": [], "page_errors": [], "failed_requests": [], "http_errors": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_args: dict = {"color_scheme": "dark" if a.dark else "light"}
        if a.mobile:
            ctx_args.update(p.devices["iPhone 13"])
            ctx_args.pop("default_browser_type", None)
        else:
            ctx_args["viewport"] = {"width": a.width, "height": a.height}
        ctx = browser.new_context(**ctx_args)
        page = ctx.new_page()
        page.on("console", lambda m: m.type in ("error", "warning") and report["console"].append(
            {"type": m.type, "text": m.text[:500], "location": m.location.get("url", "")}))
        page.on("pageerror", lambda e: report["page_errors"].append(str(e)[:800]))
        page.on("requestfailed", lambda r: report["failed_requests"].append(
            {"url": r.url[:300], "error": r.failure or ""}))
        page.on("response", lambda r: r.status >= 400 and report["http_errors"].append(
            {"url": r.url[:300], "status": r.status}))

        t0 = time.time()
        try:
            resp = page.goto(a.url, wait_until=a.wait, timeout=45000)
            report["status"] = resp.status if resp else None
        except Exception as exc:  # report and still try to capture state
            report["navigation_error"] = str(exc)[:500]
        report["load_seconds"] = round(time.time() - t0, 2)
        page.wait_for_timeout(a.settle_ms)
        report["final_url"] = page.url
        report["viewport"] = page.viewport_size

        try:
            report["checks"] = page.evaluate(CHECKS_JS)
        except Exception as exc:
            report["checks_error"] = str(exc)[:300]

        if a.tab:
            order = []
            for _ in range(a.tab):
                page.keyboard.press("Tab")
                order.append(page.evaluate(FOCUS_JS))
            report["tab_order"] = order

        if a.axe:
            try:
                page.add_script_tag(url=AXE_URL)
                res = page.evaluate("async () => (await axe.run(document, {resultTypes: ['violations']})).violations")
                report["axe_violations"] = [
                    {"id": v["id"], "impact": v["impact"], "help": v["help"],
                     "nodes": [n["target"] for n in v["nodes"][:5]]} for v in res]
            except Exception as exc:
                report["axe_error"] = f"axe-core could not run (offline or CSP?): {str(exc)[:200]}"

        tag = "mobile" if a.mobile else f"{page.viewport_size['width']}w"
        shot = out_dir / f"probe_{tag}{'_dark' if a.dark else ''}.png"
        page.screenshot(path=str(shot), full_page=a.full_page)
        report["screenshot"] = str(shot.resolve())
        browser.close()

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

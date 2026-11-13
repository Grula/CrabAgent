#!/usr/bin/env python3
"""Screenshot a page at desktop and phone widths in light and dark mode.

Accepts a URL or a local .html path. Saves PNGs and prints, per shot, the path, any
horizontal overflow (page wider than the viewport), elements that stick out past the right
edge, console errors, and web fonts that failed to load.

Usage:
  python shoot.py page.html|URL [--out design-shots] [--widths 1440,390] [--full-page]
                                 [--themes light,dark]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("playwright is not installed: pip install playwright && playwright install chromium")

INSPECT_JS = r"""
async () => {
  await document.fonts.ready;
  const vw = document.documentElement.clientWidth;
  const offenders = [...document.querySelectorAll('body *')].filter(el => {
    const r = el.getBoundingClientRect(); return r.width > 0 && r.right > vw + 1;
  }).slice(0, 8).map(el => `${el.tagName.toLowerCase()}${el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : ''} (right=${Math.round(el.getBoundingClientRect().right)})`);
  const failedFonts = [...document.fonts].filter(f => f.status === 'error').map(f => f.family);
  const bodyBg = getComputedStyle(document.body).backgroundColor;
  return { overflow_px: Math.max(0, document.documentElement.scrollWidth - vw), offenders, failedFonts, bodyBg };
}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("--out", default="design-shots")
    ap.add_argument("--widths", default="1440,390")
    ap.add_argument("--themes", default="light,dark")
    ap.add_argument("--full-page", action="store_true")
    a = ap.parse_args()

    target = a.target
    if not target.startswith(("http://", "https://", "file://")):
        target = Path(target).resolve().as_uri()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for theme in [t.strip() for t in a.themes.split(",") if t.strip()]:
            for w in [int(x) for x in a.widths.split(",")]:
                phone = w < 600
                ctx = browser.new_context(viewport={"width": w, "height": 844 if phone else 900},
                                          color_scheme=theme, device_scale_factor=2 if phone else 1,
                                          is_mobile=phone, has_touch=phone)
                page = ctx.new_page()
                errors: list[str] = []
                page.on("console", lambda m, e=errors: m.type == "error" and e.append(m.text[:300]))
                page.on("pageerror", lambda x, e=errors: e.append(str(x)[:300]))
                page.goto(target, wait_until="networkidle", timeout=45000)
                info = page.evaluate(INSPECT_JS)
                path = out / f"{w}w-{theme}.png"
                page.screenshot(path=str(path), full_page=a.full_page)
                results.append({"shot": str(path.resolve()), "width": w, "theme": theme, **info, "errors": errors})
                ctx.close()
        browser.close()

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Static checks for an HTML email, plus optional preview screenshots.

Flags things common email clients strip or mis-render: scripts, forms, external CSS, web
fonts, flex/grid/position, missing alt or relative image URLs, dead links, missing preheader,
lang, color-scheme meta, tables without role="presentation", Gmail's ~102 KB clipping limit,
and leftover {{PLACEHOLDERS}}.

Usage:
  python email_check.py email.html [--shots DIR]

--shots renders the file at 700 px (desktop reading pane) and 375 px (phone) in light and dark (needs playwright). A browser
preview is only an approximation; real clients (Outlook, Gmail) differ.
Exit code 1 if any error is found.
"""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


class Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict]] = []
        self.style_blocks: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {k: (v or "") for k, v in attrs}))
        self._in_style = tag == "style"

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.style_blocks.append(data)


def check(path: Path) -> tuple[list[str], list[str]]:
    raw = path.read_bytes()
    html = raw.decode("utf-8", errors="replace")
    s = Scan()
    s.feed(html)
    err: list[str] = []
    warn: list[str] = []
    tags = s.tags
    names = [t for t, _ in tags]

    kb = len(raw) / 1024
    if kb > 102:
        err.append(f"{kb:.0f} KB: Gmail clips messages over ~102 KB")
    elif kb > 80:
        warn.append(f"{kb:.0f} KB: close to Gmail's ~102 KB clipping limit")

    for bad in ("script", "form", "iframe", "video", "object", "embed", "svg"):
        if bad in names:
            (err if bad in ("script", "form", "iframe") else warn).append(
                f"<{bad}> is stripped or unsupported in many email clients")
    for t, a in tags:
        if t == "link" and "stylesheet" in a.get("rel", ""):
            err.append(f"external stylesheet will be dropped: {a.get('href', '')[:80]}")
    if re.search(r"@import|@font-face", html):
        warn.append("@import/@font-face: web fonts load only in a few clients; keep a safe fallback stack")

    inline = " ".join(a.get("style", "") for _, a in tags)
    for prop, msg in (
        (r"display\s*:\s*(flex|grid)", "flex/grid layout is unsupported in Outlook and some webmail"),
        (r"position\s*:\s*(absolute|fixed|relative)", "CSS positioning is stripped by many clients"),
        (r"float\s*:", "floats are unreliable; use table cells"),
        (r"var\(--", "CSS variables are unsupported in Gmail/Outlook"),
    ):
        if re.search(prop, inline, re.I):
            err.append(msg)
    if any(re.search(r"display\s*:\s*(flex|grid)", b, re.I) for b in s.style_blocks):
        warn.append("flex/grid inside <style>: fine only as progressive enhancement")

    html_tag = next((a for t, a in tags if t == "html"), {})
    if not html_tag.get("lang"):
        err.append('<html> is missing lang="…"')
    metas = [a for t, a in tags if t == "meta"]
    if not any(m.get("name") == "color-scheme" for m in metas):
        warn.append('no <meta name="color-scheme" content="light dark">; dark-mode handling will be client-guesswork')
    if not any(m.get("name") == "viewport" for m in metas):
        warn.append("no viewport meta; phones may render a shrunken desktop layout")
    if "title" not in names:
        warn.append("no <title>")

    body_i = names.index("body") if "body" in names else -1
    first = tags[body_i + 1] if 0 <= body_i < len(tags) - 1 else None
    if not first or "display:none" not in first[1].get("style", "").replace(" ", ""):
        warn.append("no hidden preheader as the first element in <body>")

    tables = [a for t, a in tags if t == "table"]
    no_role = sum(1 for a in tables if a.get("role") != "presentation")
    if no_role:
        warn.append(f'{no_role} <table> without role="presentation" (screen readers announce them as data tables)')
    for a in tables:
        w = a.get("width", "")
        if w.isdigit() and int(w) > 640:
            warn.append(f"table width={w}: keep the content column at about 600 px")

    for t, a in tags:
        if t == "img":
            src = a.get("src", "")
            if "alt" not in a:
                err.append(f"<img> without alt: {src[:60]}")
            if src and not src.startswith(("https://", "cid:", "{{")):
                err.append(f"image src must be an absolute https URL (or cid:): {src[:60]}")
            if not a.get("width"):
                warn.append(f"<img> without width attribute (Outlook may render it at full size): {src[:60]}")
        if t == "a":
            href = a.get("href", "")
            if href in ("", "#") or href.startswith("javascript:"):
                err.append(f"dead link href='{href}'")
            elif href.startswith("http://"):
                warn.append(f"non-https link: {href[:60]}")
        if t == "button" or (t == "input"):
            err.append(f"<{t}> does not work in email; use a link styled as a button")

    if re.search(r"unsubscribe", html, re.I) is None:
        warn.append("no unsubscribe text: required for marketing/bulk email")
    leftovers = sorted(set(re.findall(r"\{\{[^}]{1,60}\}\}", html)))
    if leftovers:
        warn.append("unfilled placeholders: " + ", ".join(leftovers[:8]))
    return err, warn


def shots(path: Path, out: Path) -> None:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for scheme in ("light", "dark"):
            for w in (700, 375):
                ctx = b.new_context(viewport={"width": w, "height": 900}, color_scheme=scheme,
                                    device_scale_factor=2 if w < 600 else 1)
                pg = ctx.new_page()
                pg.goto(path.resolve().as_uri())
                f = out / f"email-{w}-{scheme}.png"
                pg.screenshot(path=str(f), full_page=True)
                print(f"shot: {f.resolve()}")
                ctx.close()
        b.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--shots", default="")
    a = ap.parse_args()
    path = Path(a.file)
    err, warn = check(path)
    for e in err:
        print(f"ERROR   {e}")
    for w in warn:
        print(f"warning {w}")
    print(f"{len(err)} error(s), {len(warn)} warning(s)")
    if a.shots:
        shots(path, Path(a.shots))
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())

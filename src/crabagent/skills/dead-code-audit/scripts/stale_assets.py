"""List built asset files that the served pages can no longer reach. Read-only.

usage: stale_assets.py <served dir> [--pages index.html other.html] [--assets assets] [--dist <fresh build dir>]

Build tools write content-hashed files (app-3f9a1c.js). When a build is copied
over an older one without clearing it, the old hashes pile up. This starts at
the HTML pages, follows every asset file name mentioned in a reached file (JS
dynamic imports, CSS, fonts, workers) and lists what is left over.

With --dist, it also checks that the reachable set equals the fresh build's
asset list, which is the proof that nothing reachable is missing.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

NAME_RE = re.compile(r"[A-Za-z0-9_.\-]+\.(?:js|mjs|css|woff2?|ttf|otf|svg|png|jpe?g|gif|webp|avif|wasm|json|map)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("served", type=Path)
    ap.add_argument("--pages", nargs="*", default=None)
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--dist", type=Path)
    args = ap.parse_args()
    assets = args.served / args.assets
    names = {p.name for p in assets.iterdir() if p.is_file()}
    pages = [args.served / p for p in args.pages] if args.pages else sorted(args.served.glob("*.html"))

    reached: set[str] = set()
    queue = list(pages)
    seen: set[Path] = set()
    while queue:
        f = queue.pop()
        if f in seen or not f.exists():
            continue
        seen.add(f)
        for m in NAME_RE.findall(f.read_text(encoding="utf-8", errors="ignore")):
            if m in names and m not in reached:
                reached.add(m)
                queue.append(assets / m)

    stale = sorted(names - reached)
    size = sum((assets / n).stat().st_size for n in stale)
    print(f"pages: {', '.join(p.name for p in pages)}")
    print(f"asset files: {len(names)}  reachable: {len(reached)}  stale: {len(stale)} ({size / 1e6:.1f} MB)")
    if args.dist:
        dist = {p.name for p in (args.dist / args.assets).iterdir() if p.is_file()}
        print(f"reachable set equals fresh build: {reached == dist}")
        if reached != dist:
            print(f"  in build, not reachable: {sorted(dist - reached)[:10]}")
            print(f"  reachable, not in build: {sorted(reached - dist)[:10]}")
    for n in stale:
        print(f"  {n}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Detect front-end memory leaks by repeating a UI action and watching what survives GC.

Loads URL, takes a baseline (after forced garbage collection), repeats an action N times,
returns to the starting state, forces GC again, and compares: JS heap size, DOM node count,
event-listener count, documents/frames, plus (with --snapshot) a per-constructor diff of heap
snapshots including detached DOM nodes.

The action is either a list of clicks (--click SELECTOR, repeatable, run in order each
iteration) or a Python file defining `def action(page): ...` (and optionally
`def setup(page): ...`), passed with --action-file. The action should end where it started
(open then close a dialog, navigate to a route then back) so that anything still growing is
retained garbage.

Usage:
  python mem_probe.py URL --click "#open" --click "#close" [--iterations 10] [--snapshot DIR]
  python mem_probe.py URL --action-file flow.py --iterations 20 --snapshot snaps

Requires playwright + chromium. Prints JSON to stdout.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("playwright is not installed: pip install playwright && playwright install chromium")

WANTED = ("JSHeapUsedSize", "Nodes", "JSEventListeners", "Documents", "Frames", "LayoutObjects")


def metrics(cdp) -> dict:
    for _ in range(2):
        cdp.send("HeapProfiler.collectGarbage")
    raw = cdp.send("Performance.getMetrics")["metrics"]
    m = {x["name"]: x["value"] for x in raw if x["name"] in WANTED}
    m["JSHeapUsedMB"] = round(m.pop("JSHeapUsedSize", 0) / 1048576, 2)
    return m


def take_snapshot(cdp, path: Path) -> dict:
    chunks: list[str] = []
    handler = lambda e: chunks.append(e["chunk"])  # noqa: E731
    cdp.on("HeapProfiler.addHeapSnapshotChunk", handler)
    cdp.send("HeapProfiler.collectGarbage")
    cdp.send("HeapProfiler.takeHeapSnapshot", {"reportProgress": False})
    cdp.remove_listener("HeapProfiler.addHeapSnapshotChunk", handler)
    text = "".join(chunks)
    path.write_text(text, encoding="utf-8")
    return summarize(json.loads(text))


def summarize(snap: dict) -> dict:
    meta = snap["snapshot"]["meta"]
    fields = meta["node_fields"]
    types = meta["node_types"][0]
    nf = len(fields)
    i_type, i_name, i_size = fields.index("type"), fields.index("name"), fields.index("self_size")
    i_det = fields.index("detachedness") if "detachedness" in fields else None
    nodes, strings = snap["nodes"], snap["strings"]
    count: Counter = Counter()
    size: Counter = Counter()
    detached: Counter = Counter()
    for k in range(0, len(nodes), nf):
        t = types[nodes[k + i_type]]
        if t not in ("object", "closure", "native", "array", "regexp"):
            continue
        name = strings[nodes[k + i_name]][:80]
        key = f"{t}:{name}"
        count[key] += 1
        size[key] += nodes[k + i_size]
        if i_det is not None and nodes[k + i_det] == 2:
            detached[name] += 1
    return {"count": count, "size": size, "detached": detached}


def diff(a: dict, b: dict, top: int = 15) -> dict:
    grow = [(k, b["count"][k] - a["count"].get(k, 0), b["size"][k] - a["size"].get(k, 0)) for k in b["count"]]
    grow = [g for g in grow if g[1] > 0]
    by_count = sorted(grow, key=lambda g: -g[1])[:top]
    return {
        "top_growth_by_count": [{"constructor": k, "added": n, "added_kb": round(s / 1024, 1)} for k, n, s in by_count],
        "detached_dom_before": sum(a["detached"].values()),
        "detached_dom_after": sum(b["detached"].values()),
        "detached_dom_top": b["detached"].most_common(10),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--click", action="append", default=[], help="selector to click, in order, each iteration")
    ap.add_argument("--action-file", default="")
    ap.add_argument("--iterations", type=int, default=10)
    ap.add_argument("--pause-ms", type=int, default=300, help="wait after each iteration")
    ap.add_argument("--snapshot", default="", help="directory for baseline/final .heapsnapshot files")
    a = ap.parse_args()
    if not a.click and not a.action_file:
        sys.exit("give --click SELECTOR (repeatable) or --action-file flow.py")

    setup = action = None
    if a.action_file:
        spec = importlib.util.spec_from_file_location("flow", a.action_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        action, setup = getattr(mod, "action"), getattr(mod, "setup", None)

    def run_clicks(page):
        for sel in a.click:
            page.locator(sel).first.click()

    act = action or run_clicks
    out: dict = {"url": a.url, "iterations": a.iterations, "samples": []}
    snap_dir = Path(a.snapshot) if a.snapshot else None
    if snap_dir:
        snap_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(a.url, wait_until="load", timeout=60000)
        cdp = page.context.new_cdp_session(page)
        cdp.send("Performance.enable")
        if setup:
            setup(page)
        act(page)  # warm-up: first run allocates caches, lazy modules, etc.
        page.wait_for_timeout(a.pause_ms)
        base = metrics(cdp)
        base_snap = take_snapshot(cdp, snap_dir / "baseline.heapsnapshot") if snap_dir else None
        for i in range(a.iterations):
            act(page)
            page.wait_for_timeout(a.pause_ms)
            if i in (a.iterations // 2 - 1, a.iterations - 1):
                out["samples"].append({"after_iteration": i + 1, **metrics(cdp)})
        final = metrics(cdp)
        if snap_dir:
            final_snap = take_snapshot(cdp, snap_dir / "final.heapsnapshot")
            out["heap_diff"] = diff(base_snap, final_snap)
            out["snapshots"] = [str((snap_dir / f).resolve()) for f in ("baseline.heapsnapshot", "final.heapsnapshot")]
        browser.close()

    out["baseline"], out["final"] = base, final
    n = max(1, a.iterations)
    out["growth_per_iteration"] = {k: round((final.get(k, 0) - base.get(k, 0)) / n, 3) for k in base}
    g = out["growth_per_iteration"]
    out["verdict"] = ("likely leak" if g.get("Nodes", 0) >= 1 or g.get("JSEventListeners", 0) >= 1
                      or g.get("JSHeapUsedMB", 0) >= 0.05 else "no clear growth")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

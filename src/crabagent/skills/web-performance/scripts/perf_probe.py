#!/usr/bin/env python3
"""Measure page-load performance in headless Chromium (lab data) and explain LCP.

Reports TTFB, FCP, LCP (element, resource, four-part breakdown), CLS, long tasks / TBT
estimate, the heaviest and render-blocking resources, and common LCP mistakes found in the
DOM. Runs N times and reports the median run.

Usage:
  python perf_probe.py URL [--runs 3] [--mobile] [--throttle] [--cpu 4]
                           [--top 10] [--json-out FILE]

--throttle applies a slow-4G-like network (150 ms RTT, ~1.6 Mbps down) and --cpu sets a
CPU slowdown factor (default 4 when --throttle is set). Requires playwright + chromium.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("playwright is not installed: pip install playwright && playwright install chromium")

# Installed before any page script runs, so buffered observers see everything.
INIT_JS = r"""
window.__perf = { lcp: null, cls: 0, longtasks: [] };
try {
  new PerformanceObserver(l => { const e = l.getEntries(); window.__perf.lcp = e[e.length - 1]; })
    .observe({ type: 'largest-contentful-paint', buffered: true });
  new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__perf.cls += e.value; })
    .observe({ type: 'layout-shift', buffered: true });
  new PerformanceObserver(l => { for (const e of l.getEntries()) window.__perf.longtasks.push([e.startTime, e.duration]); })
    .observe({ type: 'longtask', buffered: true });
} catch (e) {}
"""

COLLECT_JS = r"""
() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const fcp = performance.getEntriesByName('first-contentful-paint')[0];
  const lcp = window.__perf.lcp;
  const res = performance.getEntriesByType('resource');
  const ttfb = nav.responseStart || 0;
  let lcpInfo = null;
  if (lcp) {
    const el = lcp.element;
    const r = lcp.url ? res.find(x => x.name === lcp.url) : null;
    const lcpTime = lcp.renderTime || lcp.loadTime || lcp.startTime;
    const reqStart = r ? (r.requestStart || r.startTime) : ttfb;
    const respEnd = r ? r.responseEnd : ttfb;
    lcpInfo = {
      time: lcpTime, size: lcp.size, url: lcp.url || null,
      element: el ? el.outerHTML.slice(0, 200) : null,
      breakdown: {
        ttfb: ttfb,
        resource_load_delay: Math.max(0, reqStart - ttfb),
        resource_load_duration: Math.max(0, respEnd - reqStart),
        element_render_delay: Math.max(0, lcpTime - Math.max(respEnd, ttfb)),
      },
      resource_from_cache: r ? r.transferSize === 0 && r.decodedBodySize > 0 : null,
    };
  }
  const fcpTime = fcp ? fcp.startTime : null;
  const tbt = window.__perf.longtasks
    .filter(([s]) => fcpTime === null || s >= fcpTime)
    .reduce((a, [, d]) => a + Math.max(0, d - 50), 0);
  const issues = [];
  const vh = innerHeight;
  document.querySelectorAll('img').forEach(img => {
    const b = img.getBoundingClientRect();
    if (b.top >= vh || b.width === 0) return;
    if (img.loading === 'lazy') issues.push({ issue: 'lazy-loaded image in first viewport', el: img.outerHTML.slice(0, 160) });
    if (b.width * b.height > 50000 && !img.getAttribute('fetchpriority'))
      issues.push({ issue: 'large above-the-fold image without fetchpriority="high"', el: img.outerHTML.slice(0, 160) });
    if (img.naturalWidth > 2.5 * b.width * devicePixelRatio)
      issues.push({ issue: `image intrinsic ${img.naturalWidth}px vs displayed ${Math.round(b.width)}px`, el: img.outerHTML.slice(0, 160) });
  });
  document.querySelectorAll('head script[src]:not([async]):not([defer]):not([type=module])')
    .forEach(s => issues.push({ issue: 'synchronous script in <head>', el: s.outerHTML.slice(0, 160) }));
  if (lcp && lcp.element && /^(DIV|SECTION|HEADER)$/.test(lcp.element.tagName) && lcp.url)
    issues.push({ issue: 'LCP is a CSS background image (not discoverable by the preload scanner)', el: lcp.url });
  const byWeight = res.map(r => ({ url: r.name.slice(0, 150), type: r.initiatorType,
      kb: Math.round((r.transferSize || r.encodedBodySize || 0) / 1024), ms: Math.round(r.duration),
      start: Math.round(r.startTime), blocking: r.renderBlockingStatus === 'blocking' }));
  return {
    ttfb, fcp: fcpTime, dom_content_loaded: nav.domContentLoadedEventEnd || null, load: nav.loadEventEnd || null,
    cls: window.__perf.cls, long_tasks: window.__perf.longtasks.length, tbt_estimate: tbt,
    lcp: lcpInfo, issues, resources: byWeight,
    totals: { requests: res.length + 1, transfer_kb: Math.round((res.reduce((a, r) => a + (r.transferSize || 0), 0) + (nav.transferSize || 0)) / 1024) },
  };
}
"""


def one_run(p, a) -> dict:
    browser = p.chromium.launch(headless=True)
    if a.mobile:
        desc = dict(p.devices["Pixel 7"])
        desc.pop("default_browser_type", None)
        ctx = browser.new_context(**desc)
    else:
        ctx = browser.new_context(viewport={"width": 1350, "height": 940})
    page = ctx.new_page()
    page.add_init_script(INIT_JS)
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    if a.throttle:
        cdp.send("Network.emulateNetworkConditions", {
            "offline": False, "latency": 150, "downloadThroughput": 1.6e6 / 8, "uploadThroughput": 750e3 / 8})
    cpu = a.cpu or (4 if a.throttle else 1)
    if cpu > 1:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": cpu})
    resp = page.goto(a.url, wait_until="load", timeout=90000)
    page.wait_for_timeout(a.settle_ms)
    data = page.evaluate(COLLECT_JS)
    data["status"] = resp.status if resp else None
    browser.close()
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--throttle", action="store_true")
    ap.add_argument("--cpu", type=float, default=0)
    ap.add_argument("--settle-ms", type=int, default=2500, help="wait after load so LCP/CLS settle")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    with sync_playwright() as p:
        runs = [one_run(p, a) for _ in range(max(1, a.runs))]

    def lcp_t(r):
        return r["lcp"]["time"] if r.get("lcp") else float("inf")

    ordered = sorted(runs, key=lcp_t)
    median = ordered[len(ordered) // 2]
    res = median.pop("resources")
    median["heaviest_resources"] = sorted(res, key=lambda r: -r["kb"])[: a.top]
    median["slowest_resources"] = sorted(res, key=lambda r: -r["ms"])[: a.top]
    median["render_blocking"] = [r for r in res if r["blocking"]]
    median["lcp_all_runs_ms"] = [round(lcp_t(r)) if lcp_t(r) != float("inf") else None for r in runs]
    median["fcp_all_runs_ms"] = [round(r["fcp"]) if r.get("fcp") else None for r in runs]
    median["conditions"] = {"mobile": a.mobile, "network_throttle": a.throttle,
                            "cpu_slowdown": a.cpu or (4 if a.throttle else 1), "cache": "disabled"}
    if median.get("lcp"):
        b = median["lcp"]["breakdown"]
        total = sum(b.values()) or 1
        median["lcp"]["breakdown_pct"] = {k: round(100 * v / total) for k, v in b.items()}
        median["lcp"]["rating"] = "good" if lcp_t(median) <= 2500 else "needs improvement" if lcp_t(median) <= 4000 else "poor"
    median["spread_ms"] = (round(statistics.pstdev([lcp_t(r) for r in runs if lcp_t(r) != float("inf")]))
                           if len(runs) > 1 and all(r.get("lcp") for r in runs) else None)

    def rnd(o):
        if isinstance(o, float):
            return round(o, 3) if o < 1 else round(o)
        if isinstance(o, dict):
            return {k: rnd(v) for k, v in o.items()}
        if isinstance(o, list):
            return [rnd(v) for v in o]
        return o

    text = json.dumps(rnd(median), indent=2, ensure_ascii=False)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

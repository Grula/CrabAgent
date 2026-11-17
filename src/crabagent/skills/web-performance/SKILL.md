---
name: web-performance
description: Diagnose slow web pages and memory leaks: measure LCP, FCP, CLS, TTFB and blocking time with headless Chromium or Lighthouse, find the bottleneck, fix and re-measure. Use for "page is slow", "Core Web Vitals", "Lighthouse score", "memory keeps growing".
---

# Web Performance

Measure first, find the single biggest bottleneck, fix it, measure again. If your role or
brief says not to change code, stop after finding the bottleneck and report it with the
numbers. The `browser_*` tools
can't read performance timings, throttle, or take heap snapshots, so all measurement here runs
through `bash` with the bundled Playwright scripts (or Lighthouse when Node is available).

## Prerequisites

```bash
python -c "import playwright" && echo ok      # CrabAgent's browser extra provides this
playwright install chromium                    # if the browser binary is missing
node --version                                 # optional, only for Lighthouse
```

If the target is a local app, start it in **production mode** (`npm run build && npm run preview`,
`next build && next start`, etc.). Dev servers ship unminified code and hot-reload clients, so
their numbers are misleading. Note in the report which mode you measured.

## Workflow A — slow load / LCP

1. **Baseline** (3 runs, median reported):
   ```bash
   python <skill-dir>/scripts/perf_probe.py <url> --runs 3 --json-out perf-before.json
   python <skill-dir>/scripts/perf_probe.py <url> --runs 3 --mobile --throttle   # phone-like
   ```
   Thresholds: LCP ≤ 2.5 s good, ≤ 4 s needs work; CLS ≤ 0.1 good; TBT (lab proxy for
   responsiveness) ≤ 200 ms good. Lab numbers are not field data — say so.
2. **Identify the LCP element** from `lcp.element` / `lcp.url`. Empty `url` means it is text, so
   its delay comes from HTML, CSS or fonts rather than an image download.
3. **Find the bottleneck** in `lcp.breakdown_pct`. Rough healthy shape: TTFB and resource load
   duration take most of the time; the two *delay* parts should each be under ~10%.

   | Dominant part | Usually means | Look at |
   |---|---|---|
   | `ttfb` | slow server, redirects, no caching | server timing, `curl -w '%{time_starttransfer}\n' -o /dev/null -s <url>` |
   | `resource_load_delay` | LCP resource discovered late (lazy-loaded, injected by JS, CSS background, low priority) | `issues`, the `<img>` markup |
   | `resource_load_duration` | file too big or slow origin | `heaviest_resources`, image format/size |
   | `element_render_delay` | render-blocking CSS/JS, long tasks, client-side rendering | `render_blocking`, `long_tasks`, `tbt_estimate` |

   Fix the dominant part first. Shrinking an image does nothing if render delay is the real
   problem — the time just moves.
4. **Read the code** responsible (`grep` for the LCP image, the blocking script, the font loader)
   and apply fixes from `references/lcp-fixes.md`.
5. **Verify**: rerun the same command with the same flags and compare against `perf-before.json`.
   Report before/after for LCP, its breakdown, FCP, CLS and TBT. If the spread between runs
   (`lcp_all_runs_ms`) is larger than the improvement, run more times before claiming a win.

### Optional: Lighthouse

When Node is available and the user wants a score or the full audit list:

```bash
export CHROME_PATH=$(python -c "from playwright.sync_api import sync_playwright as s; p=s().start(); print(p.chromium.executable_path); p.stop()")
npx --yes lighthouse <url> --only-categories=performance --output=json \
  --output-path=lh.json --chrome-flags="--headless=new" --quiet
node -e "const r=require('./lh.json');console.log('score',r.categories.performance.score);
for (const a of Object.values(r.audits)) if (a.score!==null && a.score<0.9 && a.details)
  console.log(a.id,'|',a.displayValue||'','|',a.title)"
```

Don't `read` the whole report — it is megabytes. Filter it as above, then inspect one audit at a
time (`r.audits['<id printed above>'].details.items`; audit ids differ between Lighthouse
versions, so use the ones the filter printed). Lighthouse throttles to a
mid-range phone by default, so its numbers will be worse than an unthrottled probe run.

## Workflow B — memory leaks

Symptoms: tab gets slower the longer it's used, memory in the task manager keeps climbing,
"Aw, Snap" / out-of-memory crashes.

1. Pick a suspect interaction that should return to the same state: open/close a modal, navigate
   to a route and back, add/remove a list item, switch tabs.
2. Repeat it and compare after forced GC:
   ```bash
   python <skill-dir>/scripts/mem_probe.py <url> --click "#open-dialog" --click "#close-dialog" \
     --iterations 20 --snapshot mem-snaps
   ```
   For flows that need typing or waits, write `flow.py` with `def action(page): ...` (Playwright
   sync API) and pass `--action-file flow.py`.
3. Read `growth_per_iteration`. Steady growth in `Nodes`, `JSEventListeners` or heap per
   iteration is a leak; a one-time jump that then stays flat is usually a cache warming up.
4. With `--snapshot`, `heap_diff.top_growth_by_count` names the constructors that accumulated and
   `detached_dom_*` counts DOM trees removed from the page but still referenced. Map those names
   to code with `grep` (component names, class names, the element type).
   Never `read` `.heapsnapshot` files — they are huge. Give the paths to the user if they want to
   open them in browser DevTools (Memory panel → Load) to follow retainer chains.
5. Fix using `references/leak-patterns.md`, then rerun the identical command and show the growth
   dropping to ~0.

Detached DOM is sometimes intentional (cached views, virtualized lists). Point it out and ask
before removing a cache the code seems to keep on purpose.

Server-side (Node) leaks: run the server with `node --inspect` and use the same idea — repeat
requests with `curl` in a loop and watch `process.memoryUsage()` or RSS (`ps -o rss= -p <pid>`).

## Report format

```markdown
## Performance: <page>
Conditions: <desktop|mobile>, <throttled|unthrottled>, <prod|dev build>, median of N runs
| Metric | Before | After |
|---|---|---|
| LCP | 4.1 s (poor) | 2.2 s (good) |
| LCP breakdown | TTFB 12% · load delay 48% · load 30% · render 10% | … |
| CLS / TBT | 0.02 / 340 ms | 0.02 / 120 ms |
Root cause: <one or two sentences>
Changes: <files and what changed>
Remaining opportunities: <ranked, with expected impact>
```

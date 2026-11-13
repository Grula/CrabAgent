# Fixes by LCP Bottleneck

Pick the section matching the largest share in `lcp.breakdown_pct`. Change one thing at a time
and re-measure, so you know which change helped.

## Resource load delay (resource found or requested late)

- The hero image should be a plain `<img src="…">` in the server-rendered HTML. Images inserted by
  JavaScript, set via `data-src` lazy-loading libraries, or used as CSS `background-image` are
  discovered only after scripts or styles run.
- Remove `loading="lazy"` from anything visible in the first viewport. Lazy-load below the fold only.
- Add `fetchpriority="high"` to the LCP `<img>`; leave other images at default priority.
- If the resource can't be in the markup (CSS background, a font, a JS-chosen image), add
  `<link rel="preload" as="image" href="…" fetchpriority="high">` (or `as="font" crossorigin` for
  fonts) in `<head>`.
- Third-party origin for the image or font: add `<link rel="preconnect" href="https://cdn…">`, or
  self-host it.
- Framework image components (`next/image`, `nuxt-img`, etc.) usually have a `priority`/`preload`
  prop — use it for the hero instead of hand-written tags.

## Resource load duration (download takes long)

- Serve a size close to the displayed size: `srcset` + `sizes`, or resize at build time. The probe
  flags images whose intrinsic width is far above what is displayed.
- Use AVIF or WebP with sensible quality (roughly 60–80); keep a JPEG/PNG fallback via `<picture>`
  only if old browsers matter.
- Compress text assets (gzip/brotli) and set long `Cache-Control` lifetimes on fingerprinted files.
- Serve static assets from a CDN close to users.
- Reduce competition: fewer large requests in parallel with the LCP resource (defer carousels,
  below-the-fold images, non-critical fonts).

## Element render delay (downloaded but not painted)

- Render-blocking CSS: inline the small critical CSS for above-the-fold content, load the rest
  without blocking (`media="print" onload="this.media='all'"` or framework equivalents), and remove
  unused CSS.
- Render-blocking JS: add `defer` (or `async` for independent scripts) to `<script>` tags in
  `<head>`; move non-essential third-party tags (analytics, chat widgets, A/B testing) later.
- Client-side rendering: if the LCP element only exists after a JS bundle runs and fetches data,
  render it on the server (SSR/SSG) or at least ship the hero markup in the HTML.
- Long tasks on the main thread: split work with `await` points / `scheduler.yield()` /
  `requestIdleCallback`, code-split large bundles, and avoid heavy hydration of static content.
- Web fonts delaying text LCP: `font-display: swap` (or `optional`), preload the one critical
  font file, subset fonts.
- A/B testing or anti-flicker snippets that hide the page until a script loads are a common cause;
  measure with them disabled to confirm.

## Time to first byte

- Remove redirect chains (http→https→www→/en/ can add hundreds of ms).
- Cache HTML at the edge/CDN where content allows; add server-side caching for expensive queries.
- Profile the server handler: slow database queries, synchronous calls to other services,
  missing indexes.
- Stream the HTML (`<head>` first) so the browser starts fetching resources earlier.
- Keep pages eligible for the back/forward cache (no `unload` handlers, no `Cache-Control: no-store`
  unless required).

## Layout shift (CLS)

- Give images, videos and iframes `width`/`height` attributes or a CSS `aspect-ratio`.
- Reserve space for ads, embeds, cookie banners and late-loading components.
- Avoid inserting content above existing content after load; animate with `transform`, not
  `top`/`height`.
- Match fallback font metrics (`size-adjust`, `ascent-override`) to reduce swap shifts.

## Total blocking time / responsiveness

- Find large bundles with the bundler's analyzer (`npx vite-bundle-visualizer`,
  `webpack-bundle-analyzer`, `next build` output) and lazy-load routes and heavy widgets.
- Debounce expensive input handlers; move heavy computation to a Web Worker.
- Virtualize very long lists instead of rendering thousands of rows.

# Front-end Leak Patterns and Fixes

Use the constructor names from `heap_diff` and the counters from `growth_per_iteration` to pick a
pattern, then `grep` the codebase for the matching code.

| Signal | Likely pattern |
|---|---|
| `JSEventListeners` grows ~1 per iteration | Listener added on mount, never removed |
| `Nodes` grows, `detached_dom_after` > 0 | Removed elements still referenced from JS |
| A component/class name grows by exactly N | Instances kept in a module-level array, map, store or cache |
| `closure:` / `system / Context` grows | Callbacks capturing large scopes, kept alive by timers or subscriptions |
| Heap grows, nodes flat | Unbounded data cache, log buffer, or history array |

## Listeners on long-lived targets

Handlers attached to `window`, `document`, a global event bus, a WebSocket, or a store keep
everything they reference alive.

- Plain JS: keep a reference to the handler and call `removeEventListener` with the same function
  and options; or pass `{ signal }` from an `AbortController` and call `abort()` on teardown.
- React: return a cleanup from `useEffect` that removes the listener/unsubscribes.
- Vue: remove in `onBeforeUnmount`; Svelte: return cleanup from `onMount` or use `onDestroy`;
  Angular: unsubscribe in `ngOnDestroy` or use `takeUntilDestroyed`.

## Timers and observers

`setInterval`, recursive `setTimeout`, `requestAnimationFrame` loops, `ResizeObserver`,
`MutationObserver` and `IntersectionObserver` keep their callbacks alive until cleared or
disconnected. Clear/disconnect them on teardown.

## Detached DOM

Elements removed from the page but still stored in a variable, array, map, closure, or a
third-party widget instance. Null out the reference on removal, store per-element data in a
`WeakMap` keyed by the element, and call a library's `destroy()`/`dispose()` method when removing
its container (charts, editors, maps, tooltips).

Some detached trees are deliberate (view caches, recycled list rows). Confirm with the user before
removing a cache.

## Unbounded collections

Module-level arrays/maps used as caches, memoization without eviction, logs or undo history that
only grow. Cap the size (LRU), clear on route change, or key by object with `WeakMap`/`WeakRef`.

## Subscriptions and requests

Store subscriptions, RxJS streams, GraphQL/WebSocket subscriptions and in-flight `fetch` calls
whose results are written into unmounted components. Unsubscribe on teardown and abort fetches
with `AbortController`.

## Globals and console

Accidental globals (assignment without `let`/`const` in sloppy mode, `window.foo = …` for
debugging) and objects passed to `console.log` while DevTools is open stay reachable. Remove debug
globals and noisy logging of large objects.

## Verifying the fix

Rerun the identical `mem_probe.py` command. Per-iteration growth in nodes and listeners should drop
to about zero; heap may still wobble by a few KB because of JIT and internal caches.

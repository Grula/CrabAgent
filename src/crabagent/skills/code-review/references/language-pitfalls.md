# Language and framework pitfalls

Use during pass D of the review. Only flag an instance the diff introduces or re-exposes,
and only with a concrete trigger.

## Python
- Mutable default argument (`def f(x=[])`, dataclass field default without `default_factory`) shared across calls.
- Late-binding closures in loops (`lambda: i` captures the final `i`).
- `if value:` where `0`, `""`, `[]`, or `False` are legitimate values; `is` vs `==` on ints/strings.
- Bare `except:` / `except Exception:` swallowing `KeyboardInterrupt`-adjacent or real errors; `return` inside `finally` hiding exceptions.
- Iterating a dict/list while mutating it; relying on `set` ordering.
- Naive vs aware `datetime` comparison; `datetime.now()` without tz; `utcnow()` returning naive values.
- Async: calling a coroutine without `await`; blocking I/O (`requests`, `time.sleep`, file reads) inside an `async def`; `asyncio.gather` without `return_exceptions` hiding partial failure; tasks created and never awaited or referenced.
- `subprocess` with `shell=True` and interpolated input; `yaml.load` without SafeLoader; `pickle` on untrusted data.
- Integer division / float equality; `round()` banker's rounding.
- SQLAlchemy/ORM: lazy loads after session close, N+1 queries, missing `commit`/`flush`, `==` vs `is_` for NULL.

## JavaScript / TypeScript
- Falsy checks on `0`/`""`; `==` coercion; `||` defaulting where `??` was meant.
- Missing `await`, `forEach` with async callbacks (never awaited), unhandled promise rejections.
- `this` lost when passing methods as callbacks.
- Mutating props/state in place (React re-render not triggered); stale closures in hooks; missing effect dependencies; effects without cleanup.
- `parseInt` without radix; `Array.prototype.sort` without comparator on numbers.
- `JSON.parse` on unvalidated input; `Object` used as a map with user keys (`__proto__`).
- TypeScript `as` casts and non-null `!` assertions that hide a real undefined.

## C / C++
- Use after free and dangling references: a reference, pointer, `string_view` or `span` that outlives its owner; returning a reference to a local.
- Iterators, pointers and references invalidated by `push_back`, `insert`, `erase` or `rehash`, then used.
- Signed integer overflow, shifts past the width, out-of-bounds indexing, reads of uninitialised variables: all undefined behaviour.
- Ownership: a raw `new` without a matching `delete` on every path (exceptions included); a moved-from object used afterwards; double free.
- Rule of 0/3/5 broken: a resource-owning class with a default copy constructor or assignment.
- Data races; a lock held while calling unknown code; static initialisation order across translation units.
- A non-inline definition in a header (ODR, "multiple definition"); a template defined only in a `.cpp`.
- Implicit conversions: signed/unsigned comparisons, narrowing, `size_t` underflow (`v.size() - 1` on an empty vector).
- `std::vector<bool>` is not a container of `bool&`; `operator[]` on a `std::map` inserts.

## Go
- Writing to a nil map; range-variable capture in goroutines (pre-1.22); ignoring returned `err`.
- `defer` inside loops; `defer` capturing values at call time.
- Shadowed `err` with `:=` inside blocks; nil interface vs typed nil.
- Data races on maps/slices shared across goroutines; unbuffered channel deadlocks.

## Rust
- `unwrap()`/`expect()` on paths that can fail at runtime; integer overflow in release builds wrapping silently.
- Holding a lock guard across `.await`; blocking calls in async runtime.

## Java / Kotlin / C#
- `==` on strings/boxed numbers; `equals` without `hashCode`.
- Unclosed streams/connections (missing try-with-resources / `using`).
- Mutable static state; non-thread-safe collections or `SimpleDateFormat` shared across threads.

## SQL and data
- String-built queries; missing parameter binding.
- `NULL` comparisons with `=`; `NOT IN` with a NULL in the subquery.
- Migrations that are not reversible or lock large tables; adding NOT NULL without default/backfill.
- Off-by-one in pagination; missing `ORDER BY` before `LIMIT`.

## Shell
- Unquoted variables (word splitting, globbing); `set -e` not covering pipelines (no `pipefail`).
- `cd` without checking success before destructive commands; `rm -rf "$VAR/"` with empty `VAR`.

## Cross-cutting
- Time zones, DST, leap days; comparing timestamps from different clocks.
- Unicode: byte length vs character length, case-folding, normalization.
- Float money arithmetic; rounding at the wrong step.
- Path handling: joining user input into paths, Windows separators, case-insensitive filesystems.
- Config/env defaults flipped; feature flags inverted; new required settings without a default.
- Retries around non-idempotent operations; timeouts missing on network calls.

# Common Python Failures: Symptom → Cause → Fix

Each entry gives the usual cause, how to confirm it, and where the fix belongs. Fix the cause. A guard added at the crash site only hides the bug.

## `TypeError: 'NoneType' object is not subscriptable` / `AttributeError: 'NoneType' object has no attribute …`
- **Usual cause:** a function returned `None`. That happens on a code path with no `return`, a lookup that found nothing, or an in-place method such as `list.sort()` whose result was assigned.
- **Confirm:** trace the variable back to where it was assigned, and call that function with the failing input.
- **Fix:** make the producer return the right value, or handle "not found" explicitly where it can happen. Only guard at the crash site when `None` is a legitimate value there.

## `ModuleNotFoundError` / `ImportError: cannot import name …`
- **Usual causes:**
  - wrong interpreter or virtualenv
  - package not installed in that environment
  - a project file shadowing the module (`email.py`, `json.py`)
  - a circular import ("partially initialized module")
- **Confirm:** `python -c "import sys; print(sys.executable)"` and `python -c "import mod; print(mod.__file__)"`. For circular imports, read both modules' top-level imports.
- **Fix:**
  - wrong environment: use the project's interpreter or install into it
  - shadowing: rename the shadowing file
  - circular import: move the shared code into a third module, or import inside the function that needs it

## `KeyError`
- **Usual cause:** the data doesn't have the shape the code assumes. Common reasons are a renamed field, an optional field, different casing, or the wrong level of nesting.
- **Confirm:** print the actual keys (`print(sorted(d))`) right before the access.
- **Fix:** correct the producer or the key name. Use `.get(key, default)` only when the key is genuinely optional, and choose a default that means something.

## Mutable default argument
- **Symptom:** values "leak" between calls, e.g. a list keeps growing.
- **Cause:** `def f(items=[])` creates the default once and shares it between calls.
- **Fix:** `def f(items=None): items = [] if items is None else items`. The same goes for dict and set defaults, and for mutable dataclass fields (use `field(default_factory=list)`).

## Late-binding closures
- **Symptom:** callbacks created in a loop all use the last value.
- **Cause:** a closure captures the variable, not its value at creation time.
- **Fix:** bind the value as a default argument (`lambda x, i=i: …`) or use `functools.partial`.

## `RecursionError`
- **Usual cause:** a missing or unreachable base case, or two functions or properties calling each other.
- **Confirm:** the repeating frames in the traceback show the cycle.
- **Fix:** correct the base case or break the cycle. Raising the recursion limit only hides the problem.

## `UnicodeDecodeError` / mojibake
- **Usual cause:** text read with the platform's default encoding, or with the wrong one.
- **Fix:** pass `encoding="utf-8"` (or the file's real encoding) on every read and write. Use `errors="replace"` only for best-effort reads, such as logs, where losing characters is acceptable.

## `RuntimeWarning: coroutine '…' was never awaited`
- **Cause:** a coroutine function was called without `await`, so its body never ran.
- **Fix:** `await` it, or schedule it with `asyncio.create_task` and keep a reference to the task.

## Async code that is slow or frozen
- **Usual cause:** a blocking call inside async code (`time.sleep`, `requests`, heavy CPU work, sync database drivers).
- **Confirm:** run with `python -X dev`; asyncio debug mode reports slow callbacks.
- **Fix:**
  - use the async equivalent (`asyncio.sleep`, `httpx.AsyncClient`)
  - for blocking work, `await asyncio.to_thread(fn, …)`

## `TypeError: can't compare offset-naive and offset-aware datetimes`
- **Cause:** naive and timezone-aware datetimes mixed together.
- **Fix:** decide on one convention, usually aware UTC (`datetime.now(UTC)`). Convert at the boundaries where data enters the program.

## Money or totals off by tiny amounts
- **Cause:** binary floating point, e.g. `0.1 + 0.2 != 0.3`.
- **Fix:** use `decimal.Decimal`, built from strings, or integer cents for money. Compare floats with `math.isclose`.

## `RuntimeError: dictionary changed size during iteration` / skipped list items
- **Cause:** the collection was modified while iterating over it.
- **Fix:** iterate over a copy (`for k in list(d):`), or build a new collection.

## Test passes alone but fails in the suite (or the reverse)
- **Usual cause:** shared state between tests, such as module globals, caches, environment variables, the working directory, the event loop, or a database that isn't reset.
- **Confirm:** run the test alone, then together with its neighbours. If pytest-randomly is installed, `-p no:randomly` turns random ordering off and `--randomly-seed=<n>` replays a specific order.
- **Fix:** isolate the state with fixtures (`monkeypatch`, `tmp_path`, transactional DB fixtures) instead of ordering the tests.

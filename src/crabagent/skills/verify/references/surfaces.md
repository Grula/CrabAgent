# Driving common surfaces

Each example shows: the change, what it implies, the plan, and what failure looks like.

## Command-line tool

**Change:** `export` subcommand gains `--format csv` (default stays `json`).

**Implies:** `tool export --format csv` writes CSV with a header row; `tool export` without
the flag still writes the same JSON as before; an unknown format is rejected clearly.

```bash
tool export --limit 3                 # non-regression: JSON as before
tool export --limit 3 --format csv    # new path: header + 3 rows
tool export --format xml; echo "exit=$?"   # probe: unknown value
tool export --format; echo "exit=$?"       # probe: missing value
```

Check CSV validity cheaply, e.g. `python -c "import csv,sys; print(len(list(csv.reader(sys.stdin))))"`.

**Failure looks like:** `unknown option --format` (stale build or not wired), JSON output
despite the flag, JSON default changed, stack trace instead of a usage error.

Commands that read stdin: pipe in sample data. Commands that write or delete: point them
at a `mktemp -d` directory or use a dry-run flag.

## HTTP server / API

**Change:** `POST /api/items` now returns `409` when the name already exists.

```bash
# start in the background: bash tool with background: true. It returns a PID and
# captures the output, which bash_output(pid) shows.
PORT=18080 python -m app
# wait for readiness (a separate, foreground bash call)
for i in $(seq 1 30); do curl -sf localhost:18080/health >/dev/null && break; sleep 1; done

curl -si -X POST localhost:18080/api/items -H 'content-type: application/json' -d '{"name":"a"}'   # expect 201
curl -si -X POST localhost:18080/api/items -H 'content-type: application/json' -d '{"name":"a"}'   # expect 409
curl -si -X POST localhost:18080/api/items -H 'content-type: application/json' -d '{"name":"A"}'   # probe: case
curl -si -X POST localhost:18080/api/items -H 'content-type: application/json' -d '{}'             # probe: missing field
```

Capture status line, relevant headers, and body. Before stopping the server, call
`bash_output` with its PID and read the log for errors that never reached the response.
Then stop it with `bash_stop`.

**Failure looks like:** 500 instead of 409 (unhandled DB constraint), 201 twice (check not
reached - confirm you are hitting the new build), 409 on the first request (check inverted).

## Web front end

**Change:** the settings page shows an inline error when the e-mail field is invalid.

1. Start the dev server (background), wait until the URL responds.
2. `browser_navigate` to the settings page; `browser_screenshot` the initial state.
3. `browser_type` an invalid address, `browser_click` save, `browser_screenshot`.
4. Probe: fix the address and save again - error disappears, value persists after reload.
5. Use `browser_extract` to capture the error text exactly.

If the user wants to watch or needs to log in, use the `collab_browser_*` tools and
`collab_browser_wait_for_user` for the login step.

## Library

**Change:** `parse_duration("1h30m")` now supports combined units.

Write a scratch script **outside** the source tree that imports the package as a consumer
would (installed in the project environment, e.g. `pip install -e .` or `uv run`):

```python
from mypkg import parse_duration
for s in ["1h30m", "90m", "1h", "", "1h-5m", "30m1h"]:
    try:
        print(repr(s), parse_duration(s))
    except Exception as e:
        print(repr(s), "->", type(e).__name__, e)
```

Importing from `src/mypkg/...` directly skips packaging problems (missing exports, missing
files in the wheel) - use the public import path.

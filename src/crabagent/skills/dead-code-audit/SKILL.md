---
name: dead-code-audit
description: Find dead code and report it with evidence - unused functions, modules, imports and settings, code only tests use, endpoints no client calls, unused frontend files and exports, unused dependencies, stale build output. Report only. Use for a periodic cleanup audit or when asked to find dead code.
---

# Dead-Code Audit

You produce a report. You change nothing unless your brief explicitly says to apply
the removals (then see section 5).

## 1. Collect candidates
Run what fits the project. The scripts are in `<skill-dir>/scripts/`: standard library
only, read-only, `--help` for options. Read AGENTS.md or the README first for the
project's layout and how to run its Python.

| What | How |
|---|---|
| Python imports and locals | `ruff check <src> --select F401,F811,F841` (or `python3 -m pyflakes <src>`) |
| Python definitions and modules | `python3 <skill-dir>/scripts/py_unused.py --src <package dir> --tests tests --also scripts pyproject.toml <Dockerfile, *.spec>` |
| Python code the tests never run | `python3 <skill-dir>/scripts/py_coverage.py --src <package dir> -- <the project's test command>` (run it with the project's Python and PYTHONPATH; it follows the subprocesses the tests start) |
| TS/JS locals and imports | `npx tsc --noEmit --noUnusedLocals -p <tsconfig>` |
| TS/JS files and exports | `python3 <skill-dir>/scripts/ts_unused.py <frontend dir>` |
| FastAPI endpoints | `python3 <skill-dir>/scripts/fastapi_routes.py --app <pkg.module:app_or_factory> --client <frontend src> <other clients> --prefix /api` (run it with the project's PYTHONPATH; point any database setting at a throwaway file) |
| Build output copied into a served folder | `python3 <skill-dir>/scripts/stale_assets.py <served dir> --dist <fresh build dir>` |
| Dependencies | for each declared package, grep its import name (pillow -> `PIL`, python-jose -> `jose`); for npm, also check config files (vite, postcss, eslint, tailwind) |
| Settings | for each settings field, grep its name outside the settings file, plus env files, compose files and docs |

## 2. Verify every candidate
A scanner only finds names nobody mentions. Before you call anything dead, check:
- **Reached without its name:** `getattr` or a string lookup, a registry or plugin
  list, a decorator (routes, tool or command registration, fixtures), a framework hook
  (`model_post_init`, `HTMLParser.handle_*`, `__dunder__` methods), an entry point
  (`pyproject.toml`, `package.json`), a build spec, a Dockerfile, CI, docs.
- **A same-named symbol hides it:** the scanner counts names, so a function is
  "used" when an unrelated one shares its name. Grep with context for real call sites
  when an item matters.
- **Endpoints:**
  - **URLs built in pieces:** a base-path helper (`${path(id)}/groups`) or a client-side
    `${action}` segment hides the full URL. These show as TAIL ONLY or NO MATCH; read
    the client code.
  - **URLs the server hands out:** preview links, redirects, URLs inside responses.
  - **Other clients:** CLI, mobile pages, static HTML pages, scripts.
  - **One method at a time:** a path can be live for POST and dead for DELETE. Grep the
    client calls (`api.del(`, `method: "DELETE"`).
- **Only tests use it:** it's dead in production. List its tests as part of the removal.
- **The tests never run it:** that is a lead, not proof. Before calling it dead, check it
  can't be reached at all: an error path, a Ctrl-C handler or a production-only branch
  is live but untested (say so under Flags). Unreachable means no caller can get there,
  for example a check that an earlier check always makes first.
- **Tests for code that is gone:** list every file in the test folders, in any language (a
  `.cjs` or shell test next to Python ones), and check that what each imports still exists.
  The scripts above only read Python and TypeScript.
- **The whole symbol, not just its name:** state that is written but never read (a
  list appended to, a field set) is dead too, along with whatever fills it.
- **The chain:** each removal can orphan helpers, types, settings and dependencies.
  Say which items depend on others, and how many rounds of re-scanning it took until
  nothing new appeared.

Leave out anything you couldn't settle, or list it under "Unsure" with what you'd check
next.

## 3. Flag what matters more than size
Report these in a separate section, even though they are not just dead code:
- settings or code that look like a safety feature but are never used (a blocklist
  nothing enforces)
- unused endpoints that expose more than any client needs (serving any file on disk)
- live features that are questionable rather than dead: a legacy mode, an old
  packaging path, two lockfiles for one project. These need the owner's decision.

## 4. Report
```
DEAD-CODE AUDIT - <project>, <date>
Summary: <n> items, about <lines> lines; <n> endpoints; <MB> of stale build output.

Flags: <section 3 items, one line each, or "none">

Remove (certain):
- path:line  <symbol or file>  - <evidence: e.g. "no reference outside its file; not in
  routes, entry points or docs">  [+ goes with it: <tests, helpers>]
Remove (likely, check first):
- ... - <what is left to confirm>
Needs a decision:
- ... - <why it is questionable, what removing it would cost>
Unsure:
- ... - <what you would check next>
```
Group items by area (backend, frontend, build, dependencies). Evidence is what you
checked, not a restated name.

## 5. If you are asked to apply it
- Remove in dependency order, re-run the scanners after each round, and stop when they
  find nothing new.
- Delete files with plain `rm`, never `git rm`, and don't commit: the owner reviews and
  commits.
- Remove each removed item's tests, and fix tests that used it only incidentally.
- Run the full test suite and the type checker. For an app with a UI, load the main
  pages on a running copy (`run-app`) and watch for failed requests (404/405) and
  console errors.
- Report what was removed, the before and after test counts, and anything you
  deliberately left.

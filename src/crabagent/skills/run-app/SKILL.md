---
name: run-app
description: Work out how to build, run, test and check the project you're in - find its recipe, get a change live (build, restart, reload), run the right tests, and check a running copy without touching real data. Use before verifying a change, when a fix "doesn't show up", or to brief a check.
---

# Run the App

Every project has its own recipe. Find it before improvising, and write it down once
you have worked it out. A project can ship its own `run-app` skill in
`.crabagent/skills/`; that version replaces this one there, so if you are reading
this, the project has none yet.

## 1. Find the recipe
Look in this order, and stop when it is clear:
1. `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `README`.
2. Task runners: `Makefile`, `justfile`, `Taskfile`, `package.json` scripts,
   `pyproject.toml` scripts and tool sections, `Cargo.toml`, `go.mod`.
3. `docker-compose.yml`, `.devcontainer/`.
4. CI workflows (`.github/workflows/`, `.gitlab-ci.yml`). The commands CI runs are
   the ones known to work.

Use the project's own environment (see `python-env` for Python).

## 2. Get a change live
A change nobody can see isn't done. Work out what each kind of change needs here:

| Change | Usually needs |
|---|---|
| Backend code in an interpreted language | a restart, unless the process runs with auto-reload (`--reload`, nodemon, a debug mode) |
| Frontend | nothing with a hot-reloading dev server; a production build must be rebuilt and copied or deployed to wherever the server serves it from |
| Compiled code | a rebuild, then a restart |
| Config or environment variables | a restart of the process that reads them |
| What the browser shows | a hard reload (cache, service workers) |

**Never stop or restart a process you didn't start.** Ask the user. That includes
CrabAgent itself: the agents run inside it. To see a change running, start your own
copy (section 4).

## 3. Run the tests
- Use the project's test command from section 1. Run the narrowest set first (one
  file or one test); run the full suite only when the brief or the risk calls for it.
- Don't let a pipe hide the result: `cmd | tail` reports `tail`'s exit code. Use
  `set -o pipefail`, or run without the pipe.
- Type checks and linters that CI runs are part of "tests pass".

## 4. Check a running copy safely
- Start your own copy on a free port, in a temporary directory (`mktemp -d`), with a
  copy of the data. Never use the user's live data or accounts.
- In the copy, turn off side effects: outgoing email, payments, scheduled jobs,
  webhooks.
- Drive the changed path the way a user would (browser automation, `curl`, the CLI)
  and keep the evidence: output, screenshots.
- Stop everything you started.

## 5. When a fix "doesn't show up"
Check in this order: was it built? Copied or deployed? Was the process restarted?
Is the browser showing a cached copy? Are you looking at the right instance and
port? Only then suspect the fix itself.

## 6. Save the recipe
If working it out took real effort, offer to save it for the next agent as
`.crabagent/skills/run-app/SKILL.md` in the project: how to build, run and test,
what makes each kind of change live, and the gotchas you hit.

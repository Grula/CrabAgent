---
name: project-scaffolding
description: Start a brand-new project in the right place - pick a safe project root (never dump files into a home or catch-all folder), use the ecosystem's normal layout, and scaffold the smallest runnable version. Use when the user explicitly asks to create, start, scaffold, or bootstrap a new app, tool, library, or game.
---

# New Project Scaffolding

Choose the project root and its layout **before the first write**, then build the smallest
complete version that runs.

Not for: existing repositories or projects, bug fixes, adding a component to an existing
codebase, or requests for a single file, snippet, or paste-ready answer - keep those in the
shape the user asked for.

## 1. Confirm it's time to build

- The user's current message must actually ask to start/build/scaffold the project now.
  Earlier planning or answered questions alone don't count - if this turn is still
  discussing, discuss.
- If essential product behavior or constraints are missing or ambiguous (what it does, who
  uses it, where it must run), ask one focused question and wait (see the `grill` skill,
  quick-clarification mode). Pick technical details yourself.

## 2. Choose exactly one project root

In order of precedence:

1. **A path the user named** - use it.
2. **The current directory**, only if it is clearly meant to be this project's root: it is
   empty or contains only this project's early files, and is not a general-purpose folder.
3. **A new dedicated subdirectory** with a meaningful name derived from the product
   (`habit-tracker/`, `pdf-merge-cli/`) when the current directory is a home directory,
   Desktop/Downloads/Documents, a workspace holding unrelated items, or anything else
   that isn't plainly this project.

How much to inspect:
- Look only at what placement needs: `pwd` and a top-level listing. Don't wander into
  unrelated nearby repositories, and don't adopt one just because it exists.
- If the user said not to look at existing files, don't list or read anything - decide from
  the request and the path name alone, and default to a dedicated subdirectory.

Never add an extra wrapper directory inside the chosen root (`my-app/my-app/`).

If you discover late that you already wrote this task's files into an unsuitable root,
stop, **move** (not copy) only those files into the dedicated directory, and continue
there. Don't touch unrelated content.

## 3. Use the ecosystem's normal layout

Put entry points, manifests, sources, tests, and assets where that ecosystem expects them.
Common shapes:

| Stack | Layout |
|---|---|
| Python package/CLI | `pyproject.toml`, `src/<pkg>/__init__.py`, `src/<pkg>/__main__.py` or `cli.py`, `tests/` |
| Python script-style app | `pyproject.toml` or `requirements.txt`, `<pkg>/`, `tests/` |
| Node / TypeScript | `package.json`, `src/`, `tsconfig.json` (TS), tests per the chosen runner |
| Front-end framework | the framework's official generator (`npm create vite@latest`, etc.), run non-interactively |
| Vanilla browser app/game | `index.html` at the root plus at least one separate `.js`/`.mjs` file for the logic, `style.css` |
| Go | `go.mod`, `main.go` or `cmd/<name>/main.go`, packages in `internal/` when needed |
| Rust | `cargo new <name>` layout |

Guidelines:
- Prefer the ecosystem's official scaffolding command when one exists; pass flags so it
  doesn't prompt.
- Create a directory only when it will hold real near-term content. No placeholder files,
  empty layers, or speculative abstractions.
- Add a `.gitignore` suited to the stack and a short README with how to install and run.
- For Python, create the environment inside the project (see `python-env`).
- Don't run `git init` unless the user wants a repository (or the request clearly implies one),
  and never inside another repository.
- If the user explicitly asked for a single-file or standalone deliverable, keep it single-file.

## 4. Make it run

Scaffold the smallest complete runnable version of what was requested - real behavior, not
a hello-world - plus one basic test when the stack has a standard runner. Then run it (and
the test) to confirm it starts; use the `verify` skill for anything with a UI or server.

## 5. Hand off

Before finishing, check that every file you created for this task sits under the one chosen
root; move any strays you created (don't touch unrelated files). Then report:

- the project root path,
- the layout in a few lines,
- exact commands to install, run, and test from a fresh shell,
- what was verified and anything left to do.

<!-- Adapted from Meta muse-code skills/greenfield-project-scaffolding (Apache-2.0). Modified for CrabAgent. -->

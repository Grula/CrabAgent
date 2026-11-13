---
name: python-env
description: Set up or install into a Python environment the right way - a project-local .venv (or uv-managed env), reuse whatever env/tool the project already declares, never install into /tmp or force-install into the system interpreter. Use before creating a virtualenv, choosing pip/uv/poetry, hitting "externally-managed-environment", or writing run instructions.
---

# Python Environments

The environment is part of the project, not scratch space. Someone who opens the project
tomorrow, or clones it on another machine, should find the environment where their editor
and tools expect it.

## 1. Respect what is already there

Check before creating anything:

| You find | Use |
|---|---|
| `.venv/` (or `venv/`) in the project | activate it; install into it |
| `uv.lock` or `[tool.uv]` in `pyproject.toml` | `uv sync`, `uv run ...` |
| `poetry.lock` / `[tool.poetry]` | `poetry install`, `poetry run ...` |
| `Pipfile` | `pipenv install`, `pipenv run ...` |
| `environment.yml` | conda/mamba env from that file |
| `requirements*.txt` only | project-local `.venv` + `pip install -r ...` |
| `pdm.lock` / `hatch.toml` | `pdm` / `hatch` respectively |

Never introduce a second environment or a different installer next to an existing one. If
the declared tool is not installed, tell the user rather than silently switching tools.

## 2. Where a new environment goes

Inside the project directory: `.venv` at the project root, which nearly every editor and
tool auto-detects.

With plain `venv`:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e .         # or: pip install -r requirements.txt
```

For a new project, prefer `uv` when it is available (`uv --version`) - it manages the
project-local `.venv` and is much faster:

```bash
uv venv
uv pip install -e .                # or `uv sync` when there is a lockfile / uv project
uv run python -m yourpkg           # runs inside the env without activating
```

Make sure `.venv/` is ignored by version control (add it to `.gitignore` if the project
has one and it is missing).

Never put the environment in `/tmp`, `~/envs`, or any other location outside the project:
the user's tooling won't find it, and `/tmp` gets wiped.

## 3. "externally-managed-environment" (PEP 668)

Debian, Ubuntu, Homebrew, and others mark the system Python as externally managed, so
`pip install` outside a virtualenv fails with:

```
error: externally-managed-environment
```

That is the signal to create the project environment - not an obstacle to work around.
Do **not** pass `--break-system-packages`, set `PIP_BREAK_SYSTEM_PACKAGES`, use
`sudo pip`, or delete the `EXTERNALLY-MANAGED` marker. Those modify an interpreter the OS
owns, leave the project without its own environment, and can break other software.

For a standalone command-line tool the user wants globally (not a project dependency),
suggest `pipx install <tool>` or `uv tool install <tool>`.

## 4. Running commands

- Call tools through the environment: `.venv/bin/python -m pytest`, `uv run pytest`,
  `poetry run pytest`. Each `bash` call may start a fresh shell, so don't rely on an
  earlier `source .venv/bin/activate` still being active.
- Prefer `python -m pip` over bare `pip` so the install goes to the interpreter you think it does.
- When a package import fails, confirm which interpreter ran (`python -c "import sys; print(sys.executable)"`)
  before reinstalling anything.

## 5. Hand off commands the user can run

Write run instructions against the project environment so they work from a fresh shell in
the project directory:

```bash
source .venv/bin/activate
python -m yourpkg ...
```

or `uv run python -m yourpkg ...` (or the poetry/pipenv equivalent).

Don't hand back absolute paths into an environment outside the project
(`/tmp/whatever/bin/python -m yourpkg`) - even if they work right now, they tell the user
their project has no environment of its own.

<!-- Adapted from Meta muse-code skills/python-env (Apache-2.0). Modified for CrabAgent. -->

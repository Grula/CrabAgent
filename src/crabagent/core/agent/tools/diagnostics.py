"""Type and lint problems for the files an agent changes.

Two entry points:

* :func:`post_edit_report`: appended to every successful ``edit``/``write`` of a
  Python or JSON file. Fast checks only (``ast``, ``json``, and ruff's pyflakes
  rules when ruff is installed, ~30 ms) and only the problems the change
  *introduced*, so an agent fixes its own mistakes before running any test and
  isn't buried in a file's old problems.
* the ``diagnostics`` tool: the full check on demand, for the given files or
  every file this run changed: ruff (the project's rules) plus basedpyright or
  pyright (errors only) for Python, ``tsc`` for TypeScript. Slower (seconds), so
  coders run it once before reporting.

Every external checker is optional: missing ones are named in the result.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from crabagent.core.agent.tools.file_state import tracker_of
from crabagent.core.agent.tools.path_utils import get_workspace_root, resolve_tool_path
from crabagent.core.agent.tools.registry import registry

_PER_EDIT_TIMEOUT = 3
_MAX_PER_EDIT_LINES = 10
_TOOL_TIMEOUT = 180
_MAX_TOOL_PROBLEMS = 60
# Pyflakes rules and syntax errors: undefined names, unused imports, redefinitions.
# High signal and never a style opinion, so fine to show after every edit.
_QUICK_RUFF_RULES = "F,E9"
_PY_SUFFIXES = {".py", ".pyi"}
_TS_SUFFIXES = {".ts", ".tsx", ".mts", ".cts"}


# ── per-edit quick check ─────────────────────────────────────────────────


def _syntax_problem(text: str, filename: str) -> str | None:
    try:
        ast.parse(text, filename=filename)
    except SyntaxError as exc:
        return f"L{exc.lineno or 0} syntax error: {exc.msg}"
    return None


class CheckerFailed(Exception):
    """A checker is installed but didn't produce a result (bad config, timeout)."""


def _ruff_quick(text: str, path: Path, cwd: Path | None) -> list[tuple[str, str, int]] | None:
    """Pyflakes-level problems as (code, message, line), or None without ruff.

    Raises :class:`CheckerFailed` when ruff runs but fails: an empty result then
    would read as "no new problems".
    """
    ruff = shutil.which("ruff")
    if not ruff:
        return None
    try:
        proc = subprocess.run(
            [ruff, "check", "--no-cache", "--select", _QUICK_RUFF_RULES, "--output-format", "json",
             "--stdin-filename", str(path), "-"],
            input=text,
            capture_output=True,
            text=True,
            timeout=_PER_EDIT_TIMEOUT,
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise CheckerFailed(f"ruff timed out after {_PER_EDIT_TIMEOUT}s") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckerFailed(f"ruff could not run: {exc}") from exc
    # ruff exits 0 (clean) or 1 (problems found); anything else is its own error.
    if proc.returncode not in (0, 1):
        first = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise CheckerFailed(f"ruff failed: {first[0][:160] if first else f'exit {proc.returncode}'}")
    try:
        items = json.loads(proc.stdout or "[]")
    except ValueError as exc:
        raise CheckerFailed("ruff output unreadable") from exc
    return [
        (
            str(item.get("code") or "syntax"),
            str(item.get("message", "")),
            int((item.get("location") or {}).get("row", 0)),
        )
        for item in items
        if isinstance(item, dict)
    ]


def _valid_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _new_problems(before: list[tuple[str, str, int]], after: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    """Problems in *after* beyond those in *before*, matched by code and message (lines move)."""
    budget = Counter((code, message) for code, message, _ in before)
    fresh = []
    for code, message, line in after:
        if budget[(code, message)] > 0:
            budget[(code, message)] -= 1
        else:
            fresh.append((code, message, line))
    return fresh


def post_edit_report(path: Path, before: str | None, after: str, cwd: Path | None = None) -> str:
    """A short "Diagnostics:" block for a just-written file, or "" when not checked.

    ``before`` is the content prior to the change (None for a new file): only
    problems the change introduced are reported.
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            json.loads(after)
        except ValueError as exc:
            if before is not None and not _valid_json(before):
                return ""  # already not strict JSON (JSONC: comments, trailing commas)
            return f"\n\nDiagnostics: invalid JSON: {exc}"
        return ""
    if suffix not in _PY_SUFFIXES:
        return ""
    syntax = _syntax_problem(after, str(path))
    if syntax:
        return f"\n\nDiagnostics: {syntax}. Fix it before anything else."
    try:
        after_problems = _ruff_quick(after, path, cwd)
        if after_problems is None:
            return "\n\nDiagnostics: syntax OK (install ruff for undefined-name and import checks)."
        before_problems: list[tuple[str, str, int]] = []
        if before is not None and not _syntax_problem(before, str(path)):
            before_problems = _ruff_quick(before, path, cwd) or []
    except CheckerFailed as exc:
        return f"\n\nDiagnostics: syntax OK; lint not checked ({exc})."
    fresh = _new_problems(before_problems, after_problems)
    if not fresh:
        return "\n\nDiagnostics: no new problems."
    lines = [f"  L{line} {code} {message}" for code, message, line in fresh[:_MAX_PER_EDIT_LINES]]
    if len(fresh) > _MAX_PER_EDIT_LINES:
        lines.append(f"  ... and {len(fresh) - _MAX_PER_EDIT_LINES} more")
    return "\n\nDiagnostics: new problems from this change:\n" + "\n".join(lines)


# ── the diagnostics tool ─────────────────────────────────────────────────


async def _run(cmd: list[str], cwd: Path, timeout: int = _TOOL_TIMEOUT) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
        await proc.wait()
        raise
    out = stdout.decode("utf-8", errors="replace")
    if not out.strip():
        out = stderr.decode("utf-8", errors="replace")
    return proc.returncode or 0, out


def _rel(path: str | Path, workspace: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(workspace))
    except ValueError:
        return str(path)


def _has_pyright_config(workspace: Path) -> bool:
    if (workspace / "pyrightconfig.json").exists():
        return True
    pyproject = workspace / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return "[tool.pyright]" in text or "[tool.basedpyright]" in text


async def _check_python(files: list[Path], workspace: Path) -> tuple[list[str], list[str]]:
    """(problems, notes) for Python files: ruff with the project's rules + a type checker."""
    problems: list[str] = []
    notes: list[str] = []
    names = [str(f) for f in files]

    ruff = shutil.which("ruff")
    if ruff:
        code, out = await _run([ruff, "check", "--no-cache", "--output-format", "json", *names], workspace)
        if code not in (0, 1):
            notes.append(f"ruff failed (exit {code}): {out.strip()[:200]}")
            out = "[]"
        try:
            for item in json.loads(out or "[]"):
                row = (item.get("location") or {}).get("row", 0)
                code = item.get("code") or "syntax"
                problems.append(f"{_rel(item['filename'], workspace)}:{row}: [{code}] {item['message']}")
        except (ValueError, KeyError, TypeError):
            notes.append(f"ruff output unreadable: {out[:200]}")
    else:
        notes.append("ruff not installed (lint skipped)")
        for f in files:
            try:
                syntax = _syntax_problem(f.read_text(encoding="utf-8"), str(f))
            except (OSError, UnicodeDecodeError):
                continue
            if syntax:
                problems.append(f"{_rel(f, workspace)}: {syntax}")

    checker = shutil.which("basedpyright") or shutil.which("pyright")
    if not checker:
        notes.append("no type checker installed (pip install basedpyright)")
        return problems, notes
    with tempfile.TemporaryDirectory(prefix="crab-pyright-") as tmp:
        cmd = [checker, "--outputjson"]
        if not _has_pyright_config(workspace):
            # No project config: "standard" mode. basedpyright's default
            # ("recommended") reports dozens of style-level issues per file.
            extra = [str(workspace)] + ([str(workspace / "src")] if (workspace / "src").is_dir() else [])
            config = Path(tmp) / "pyrightconfig.json"
            config.write_text(json.dumps({"typeCheckingMode": "standard", "extraPaths": extra}), encoding="utf-8")
            cmd += ["-p", str(config)]
        _, out = await _run([*cmd, *names], workspace)
    try:
        report = json.loads(out)
    except ValueError:
        notes.append(f"type checker output unreadable: {out[:200]}")
        return problems, notes
    for diag in report.get("generalDiagnostics", []):
        if diag.get("severity") != "error":
            continue
        line = ((diag.get("range") or {}).get("start") or {}).get("line", 0) + 1
        message = str(diag.get("message", "")).split("\n", 1)[0]
        rule = diag.get("rule") or "type"
        problems.append(f"{_rel(diag.get('file', ''), workspace)}:{line}: [{rule}] {message}")
    return problems, notes


def _tsconfig_dir(path: Path, workspace: Path) -> Path | None:
    """The nearest folder with a tsconfig.json, never above or outside the workspace.

    The project's own node_modules/.bin/tsc is executed, so a file outside the
    workspace must not pick a project (and a binary) from wherever it lives.
    """
    for parent in path.parents:
        if parent != workspace and workspace not in parent.parents:
            return None
        if (parent / "tsconfig.json").exists():
            return parent
        if parent == workspace:
            return None
    return None


async def _check_typescript(files: list[Path], workspace: Path) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    projects: dict[Path, list[Path]] = {}
    for f in files:
        project = _tsconfig_dir(f, workspace)
        if project is None:
            notes.append(f"{_rel(f, workspace)}: no tsconfig.json above it inside the workspace (not type-checked)")
            continue
        projects.setdefault(project, []).append(f)
    for project, members in projects.items():
        local = project / "node_modules" / ".bin" / "tsc"
        try:
            local_ok = local.exists() and local.resolve().is_relative_to(workspace)
        except OSError:
            local_ok = False
        tsc = str(local) if local_ok else shutil.which("tsc")
        if not tsc:
            notes.append(f"{_rel(project, workspace)}: tsc not installed (npm install in that folder)")
            continue
        _, out = await _run([tsc, "--noEmit", "--pretty", "false", "-p", str(project)], project)
        wanted = {f.resolve() for f in members}
        other = 0
        for line in out.splitlines():
            if "error TS" not in line or "(" not in line:
                continue
            target = (project / line.split("(", 1)[0]).resolve()
            if target in wanted:
                problems.append(f"{_rel(target, workspace)}{line[line.index('('):]}")
            else:
                other += 1
        if other:
            notes.append(f"{_rel(project, workspace)}: {other} tsc errors in other files of the project")
    return problems, notes


@registry.register(
    name="diagnostics",
    description=(
        "Type-check and lint files: ruff and basedpyright/pyright (errors only) for Python, tsc for "
        "TypeScript. With no paths it checks every file you changed with edit/write in this run. Run it "
        "before you report a change done; fix what it finds in your files. Takes a few seconds."
    ),
    parameters={
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files to check (workspace-relative or absolute). Default: the files you changed.",
            },
        },
    },
    # It runs the project's own tsc, so it asks first like bash, unless a
    # profile (coder, tester, reviewer) allows it.
    requires_permission=True,
    metadata={"source": "builtin", "category": "file_management"},
)
async def diagnostics(paths: list[str] | None = None, context: Any = None) -> str:
    workspace = get_workspace_root(context)
    if workspace is None:
        return "Error: diagnostics requires an active session with a workspace"
    workspace = workspace.resolve()
    files: list[Path] = []
    if paths:
        for raw in paths:
            path, error = resolve_tool_path(str(raw), context)
            if error:
                return error
            assert path is not None
            if not path.is_file():
                return f"Error: not a file: {raw}"
            files.append(path.resolve())
    else:
        tracker = tracker_of(context)
        files = [Path(p) for p in (tracker.written_paths() if tracker else []) if Path(p).is_file()]
        if not files:
            return "No files changed with edit/write in this run; pass paths to check specific files."

    python = [f for f in files if f.suffix.lower() in _PY_SUFFIXES]
    typescript = [f for f in files if f.suffix.lower() in _TS_SUFFIXES]
    skipped = [f for f in files if f not in python and f not in typescript]

    problems: list[str] = []
    notes: list[str] = []
    try:
        if python:
            found, said = await _check_python(python, workspace)
            problems += found
            notes += said
        if typescript:
            found, said = await _check_typescript(typescript, workspace)
            problems += found
            notes += said
    except TimeoutError:
        return f"Error: diagnostics timed out after {_TOOL_TIMEOUT}s"
    except FileNotFoundError as exc:
        return f"Error: checker could not start: {exc}"

    checked = len(python) + len(typescript)
    lines = [f"Checked {checked} file{'s' if checked != 1 else ''}: "
             + (f"{len(problems)} problem{'s' if len(problems) != 1 else ''}." if problems else "no problems.")]
    lines += problems[:_MAX_TOOL_PROBLEMS]
    if len(problems) > _MAX_TOOL_PROBLEMS:
        lines.append(f"... and {len(problems) - _MAX_TOOL_PROBLEMS} more")
    if skipped:
        lines.append("Not checked (no checker for this type): " + ", ".join(_rel(f, workspace) for f in skipped[:10]))
    lines += [f"Note: {n}" for n in notes]
    return "\n".join(lines)

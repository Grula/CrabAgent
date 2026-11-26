"""Read-only view of what changed in the workspace's Git repository.

Lets agents that are not allowed to run shell commands (reviewers, auditors)
see a diff. The tool only ever runs ``git status``, ``git diff``, ``git show``
and ``git ls-files`` with fixed options; nothing it accepts can change the
repository or run another program.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any

from crabagent.core.agent.tools.path_utils import get_workspace_root
from crabagent.core.agent.tools.registry import registry

_MAX_OUTPUT_CHARS = 18_000
_TIMEOUT_SECONDS = 30

# A commit, branch, tag or range such as ``main``, ``HEAD~2``, ``a1b2c3d`` or
# ``main...HEAD``. It can never start with "-", so it cannot be read as an option.
_REF_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./~^@{}-]*$")

# Never page, never colour, and never run external diff or text-conversion
# programs that a repository's configuration may name.
_GIT = ("git", "--no-pager", "-c", "core.quotepath=off", "-c", "color.ui=never")
_DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv", "--no-color")


async def _git(workspace: Path, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *_GIT,
        *args,
        cwd=str(workspace),
        stdin=subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        return 1, f"git timed out after {_TIMEOUT_SECONDS}s"
    text = stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        text = (text + "\n" + stderr.decode("utf-8", errors="replace")).strip()
    return proc.returncode or 0, text


def _clip(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    omitted = len(text) - _MAX_OUTPUT_CHARS
    return (
        text[:_MAX_OUTPUT_CHARS]
        + f"\n\n[... {omitted} more characters not shown. Pass `paths` to look at fewer files, "
        "or `stat_only` for the list of changed files.]"
    )


@registry.register(
    name="git_diff",
    description=(
        "Show what changed in the workspace's Git repository, read-only. With no arguments: every "
        "uncommitted change (staged and unstaged) plus the list of new untracked files. Set `base` to "
        "compare against a branch or commit (for example \"main\" or \"main...HEAD\"), or `commit` to show "
        "one commit. Use this to review a change; it cannot modify anything."
    ),
    parameters={
        "type": "object",
        "properties": {
            "base": {
                "type": "string",
                "description": (
                    'Branch, commit or range to compare with, e.g. "main", "HEAD~1", "main...HEAD". '
                    "Default: HEAD, which shows uncommitted changes."
                ),
            },
            "commit": {
                "type": "string",
                "description": "Show this single commit (its message and diff) instead of comparing with `base`.",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only show changes in these files or directories (workspace-relative).",
            },
            "stat_only": {
                "type": "boolean",
                "description": "Only list the changed files with line counts, without the diff text. Default: false.",
                "default": False,
            },
        },
    },
    metadata={"source": "builtin", "category": "file_management"},
)
async def git_diff(
    base: str | None = None,
    commit: str | None = None,
    paths: list[str] | None = None,
    stat_only: bool = False,
    context: Any = None,
) -> str:
    workspace = get_workspace_root(context)
    if workspace is None:
        return "Error: git_diff requires an active session with a workspace"

    base = (base or "").strip()
    commit = (commit or "").strip()
    for label, ref in (("base", base), ("commit", commit)):
        if ref and not _REF_RE.match(ref):
            return f"Error: `{label}` must be a branch, commit or range such as main, HEAD~1 or main...HEAD."
    if base and commit:
        return "Error: pass either `base` or `commit`, not both."

    path_args: list[str] = []
    for raw in paths or []:
        text = str(raw).strip()
        if text:
            path_args.append(text)

    try:
        code, inside = await _git(workspace, "rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        return "Error: git is not installed on this machine."
    if code != 0 or inside.strip() != "true":
        return f"Error: {workspace} is not inside a Git repository."

    shape = ("--stat",) if stat_only else ()

    if commit:
        format_args = ("--format=commit %H%nAuthor: %an%nDate:   %ad%n%n%s%n%n%b",)
        code, output = await _git(workspace, "show", *_DIFF_OPTIONS, *format_args, *shape, commit, "--", *path_args)
        if code != 0:
            return f"Error: {output or 'git show failed'}"
        return _clip(output.strip() or f"Commit {commit} has no changes in the selected paths.")

    target = base or "HEAD"
    code, diff = await _git(workspace, "diff", *_DIFF_OPTIONS, *shape, target, "--", *path_args)
    if code != 0 and not base:
        # A repository without commits has no HEAD yet: show what is staged.
        code, diff = await _git(workspace, "diff", *_DIFF_OPTIONS, *shape, "--cached", "--", *path_args)
    if code != 0:
        return f"Error: {diff or 'git diff failed'}"

    sections: list[str] = []
    if diff.strip():
        sections.append(diff.rstrip())

    # New files that Git does not track yet never appear in a diff.
    if not base or "..." not in base:
        code, untracked = await _git(workspace, "ls-files", "--others", "--exclude-standard", "--", *path_args)
        names = [line for line in untracked.splitlines() if line.strip()] if code == 0 else []
        if names:
            shown = names[:100]
            more = f"\n... and {len(names) - len(shown)} more" if len(names) > len(shown) else ""
            sections.append(
                "New untracked files (not part of the diff above; read them in full):\n" + "\n".join(shown) + more
            )

    if not sections:
        scope = f"compared with {base}" if base else "uncommitted"
        return f"No changes ({scope})."
    return _clip("\n\n".join(sections))


_MAX_STATUS_LINES = 200


@registry.register(
    name="git_status",
    description=(
        "Show the workspace's Git status, read-only: the current branch, how far it is ahead of or behind "
        "its upstream, and every changed, staged, deleted and untracked file (short format: XY path). "
        "Cheaper than bash for this; use git_diff to see the changes themselves."
    ),
    parameters={
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only show these files or directories (workspace-relative). Default: all.",
            },
        },
    },
    metadata={"source": "builtin", "category": "file_management"},
)
async def git_status(paths: list[str] | None = None, context: Any = None) -> str:
    workspace = get_workspace_root(context)
    if workspace is None:
        return "Error: git_status requires an active session with a workspace"
    path_args = [text for text in (str(raw).strip() for raw in paths or []) if text]
    try:
        code, output = await _git(workspace, "status", "--short", "--branch", "--untracked-files=all", "--", *path_args)
    except FileNotFoundError:
        return "Error: git is not installed on this machine."
    if code != 0:
        return f"Error: {output or 'git status failed'}"
    lines = output.rstrip().splitlines()
    if len(lines) <= 1:
        return (lines[0] + "\n" if lines else "") + "Working tree clean."
    if len(lines) > _MAX_STATUS_LINES + 1:
        hidden = len(lines) - _MAX_STATUS_LINES - 1
        lines = lines[: _MAX_STATUS_LINES + 1] + [f"... and {hidden} more (pass `paths` to narrow it down)"]
    return "\n".join(lines)

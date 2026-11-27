"""Commit and open a pull request, with the guard rails a careful engineer keeps.

``git_commit`` stages and commits only the files it is given (never everything,
and never what someone else had staged), refuses to commit on the default
branch unless it is told a new branch to create, refuses likely secret files,
and never amends, force-pushes or skips hooks. ``create_pull_request`` pushes
the current branch (never the default one, never with force) and opens the PR
with the GitHub CLI.

Both only run when a request asks for them: the agent goals say so, and the
pull request tool asks the user first (it publishes the work).
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from crabagent.core.agent.tools.git import _GIT
from crabagent.core.agent.tools.path_utils import get_workspace_root
from crabagent.core.agent.tools.registry import registry

_COMMIT_TIMEOUT = 300  # hooks (pre-commit, tests) may run
_PUSH_TIMEOUT = 180
_MAX_SUBJECT = 100
_DEFAULT_BRANCHES = {"main", "master", "trunk", "develop"}
# File names a commit must never carry: credentials, keys, local secrets.
_SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_rsa*", "id_ed25519*", "*.p12", "*.pfx",
    "credentials.json", "*credentials*.json", "secrets.*", "*.secret", ".npmrc", ".pypirc",
    "settings.local.json", "encryption_key",
)
_SECRET_OK = (".env.example", ".env.sample", ".env.template")


async def _run(cmd: list[str], cwd: Path, timeout: int, env: dict | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await _kill(proc)
        return 1, f"{Path(cmd[0]).name} timed out after {timeout}s"
    except asyncio.CancelledError:
        await _kill(proc)  # a Stop must not leave a push or a hook running
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace").strip()


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass
    await proc.wait()


async def _git(workspace: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    return await _run([*_GIT, *args], workspace, timeout)


def _looks_secret(rel: str) -> bool:
    name = Path(rel).name
    if name in _SECRET_OK:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in _SECRET_PATTERNS)


async def _current_branch(workspace: Path) -> str:
    code, out = await _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if code == 0 else ""


async def _default_branch(workspace: Path) -> str | None:
    """The remote's default branch when known (origin/HEAD), else None."""
    code, out = await _git(workspace, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if code == 0 and out.strip().startswith("origin/"):
        return out.strip().split("/", 1)[1]
    return None


async def _is_default(workspace: Path, branch: str) -> bool:
    default = await _default_branch(workspace)
    return branch == default if default else branch in _DEFAULT_BRANCHES


def _valid_branch_name(name: str) -> bool:
    return bool(name) and not name.startswith("-") and " " not in name and ".." not in name


async def _resolve_paths(workspace: Path, paths: list[str]) -> tuple[list[str], str | None]:
    """Workspace-relative paths to stage, or an error. Deleted tracked files are allowed."""
    root = workspace.resolve()
    rels: list[str] = []
    for raw in paths:
        text = str(raw).strip()
        if not text:
            continue
        target = (root / text).resolve() if not Path(text).is_absolute() else Path(text).resolve()
        if not target.is_relative_to(root):
            return [], f"Error: {text} is outside the workspace."
        rel = target.relative_to(root).as_posix()
        if rel in ("", "."):
            return [], "Error: name the files to commit; staging the whole workspace isn't allowed."
        if _looks_secret(rel):
            return [], f"Error: {rel} looks like a secret (credentials, keys, .env); it is never committed."
        if not target.exists():
            code, _ = await _git(workspace, "ls-files", "--error-unmatch", "--", rel)
            if code != 0:
                return [], f"Error: {rel} doesn't exist and isn't tracked."
        rels.append(rel)
    if not rels:
        return [], "Error: paths is empty; list the files you changed."
    return rels, None


@registry.register(
    name="git_commit",
    description=(
        "Commit the files you changed, only when the user asked for a commit. Stages and commits exactly "
        "the paths you list (other staged or changed files stay out). On the default branch (main/master) "
        "pass `branch`: a new branch is created for the commit. Never amends, force-pushes or skips hooks; "
        "if a hook fails, its output comes back and nothing is committed. Message: first line says what "
        "changed (imperative, about 72 characters), then a blank line and why."
    ),
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit message: subject line, blank line, body."},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The files (or folders) to commit, workspace-relative. Deleted files count.",
            },
            "branch": {
                "type": "string",
                "description": "Create and switch to this new branch first (required on the default branch).",
            },
        },
        "required": ["message", "paths"],
    },
    requires_permission=True,
    metadata={"source": "builtin", "category": "file_management"},
)
async def git_commit(message: str, paths: list[str], branch: str | None = None, context: Any = None) -> str:
    workspace = get_workspace_root(context)
    if workspace is None:
        return "Error: git_commit requires an active session with a workspace"
    message = (message or "").strip()
    if not message:
        return "Error: message is empty."
    subject = message.splitlines()[0].strip()
    if not subject or len(subject) > _MAX_SUBJECT:
        return f"Error: the first line of the message must say what changed in at most {_MAX_SUBJECT} characters."
    try:
        code, inside = await _git(workspace, "rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        return "Error: git is not installed on this machine."
    if code != 0 or inside.strip() != "true":
        return f"Error: {workspace} is not inside a Git repository."

    rels, error = await _resolve_paths(workspace, paths or [])
    if error:
        return error

    current = await _current_branch(workspace)
    branch = (branch or "").strip()
    if branch and branch != current:
        if not _valid_branch_name(branch):
            return f"Error: {branch!r} is not a valid branch name."
        code, _ = await _git(workspace, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        if code == 0:
            return f"Error: branch {branch} already exists; switch to it with bash or pick a new name."
        code, out = await _git(workspace, "switch", "-c", branch)
        if code != 0:
            return f"Error: could not create branch {branch}: {out}"
        current = branch
    elif await _is_default(workspace, current):
        return (
            f"Error: you are on {current}, the default branch. Pass `branch` (e.g. \"fix/short-name\") to "
            "commit on a new branch."
        )

    # What `git add` would really take (a folder may hold a .env the user never meant to commit).
    code, out = await _git(workspace, "add", "--dry-run", "--", *rels)
    if code != 0:
        return f"Error: git add failed: {out}"
    for line in out.splitlines():
        added = line.strip().removeprefix("add ").strip("'\"")
        if added and _looks_secret(added):
            return f"Error: {added} looks like a secret (credentials, keys, .env); it is never committed."
    code, out = await _git(workspace, "add", "--", *rels)
    if code != 0:
        return f"Error: git add failed: {out}"
    # Paths after `--` commit only those paths: whatever else was staged stays staged, uncommitted.
    code, out = await _git(workspace, "commit", "-m", message, "--", *rels, timeout=_COMMIT_TIMEOUT)
    if code != 0:
        if "nothing to commit" in out or "no changes added" in out:
            return f"Nothing to commit: {', '.join(rels)} have no changes."
        return f"Error: the commit failed (a hook, or git itself); nothing was committed.\n{out[-3000:]}"
    code, summary = await _git(workspace, "log", "-1", "--stat", "--format=%h %s")
    return f"Committed on {current}:\n{summary}"


@registry.register(
    name="create_pull_request",
    description=(
        "Push the current branch and open a GitHub pull request, only when the user asked for one. Never "
        "pushes the default branch or with force. Needs the GitHub CLI (gh), logged in. Commit first "
        "with git_commit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "PR title: what the change does, under 72 characters."},
            "body": {
                "type": "string",
                "description": "PR description: what changed, why, and how it was tested.",
            },
            "base": {"type": "string", "description": "Branch to merge into. Default: the repository's default."},
            "draft": {"type": "boolean", "description": "Open as a draft. Default: false.", "default": False},
        },
        "required": ["title", "body"],
    },
    requires_permission=True,
    metadata={"source": "builtin", "category": "file_management"},
)
async def create_pull_request(
    title: str, body: str, base: str | None = None, draft: bool = False, context: Any = None
) -> str:
    workspace = get_workspace_root(context)
    if workspace is None:
        return "Error: create_pull_request requires an active session with a workspace"
    if not (title or "").strip():
        return "Error: title is empty."
    gh = shutil.which("gh")
    if not gh:
        return (
            "Error: the GitHub CLI (gh) isn't installed, so no pull request can be opened from here. The "
            "commit is on its branch; the user can push it and open the PR, or install gh and log in."
        )
    branch = await _current_branch(workspace)
    if not branch or branch == "HEAD":
        return "Error: not on a branch (detached HEAD)."
    if await _is_default(workspace, branch):
        return f"Error: {branch} is the default branch; commit on a new branch first (git_commit with branch)."
    code, out = await _git(workspace, "status", "--porcelain", "--untracked-files=no")
    if code == 0 and out.strip():
        return "Error: there are uncommitted changes to tracked files; commit them (or leave them out) first."

    code, out = await _git(workspace, "push", "--set-upstream", "origin", branch, timeout=_PUSH_TIMEOUT)
    if code != 0:
        return f"Error: git push failed: {out[-2000:]}"
    cmd = [gh, "pr", "create", "--title", title.strip(), "--body", body or "", "--head", branch]
    if base:
        cmd += ["--base", base.strip()]
    if draft:
        cmd.append("--draft")
    code, out = await _run(cmd, workspace, _PUSH_TIMEOUT)
    if code != 0:
        return f"Error: the branch was pushed, but gh could not open the pull request: {out[-2000:]}"
    url = next((line for line in out.splitlines() if line.startswith("http")), out.strip())
    return f"Pull request opened: {url}"

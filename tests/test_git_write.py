"""git_commit and create_pull_request (core/agent/tools/git_write.py), on real repositories."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools import git_write
from crabagent.core.agent.tools.git_write import create_pull_request, git_commit

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
_GIT = ["git", "-c", "user.name=T", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    # The tool runs plain git: give it an identity without touching the user's config.
    for key, value in (("GIT_AUTHOR_NAME", "T"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "T"),
                       ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(key, value)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([*_GIT, *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "b.py").write_text("b = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _ctx(repo: Path) -> AgentContext:
    return AgentContext(workspace=repo)


async def test_refuses_the_default_branch_without_a_new_branch(repo):
    (repo / "a.py").write_text("a = 2\n")
    result = await git_commit("Change a", ["a.py"], context=_ctx(repo))
    assert result.startswith("Error: you are on main") and "branch" in result
    assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1"


async def test_commits_only_the_named_files_on_a_new_branch(repo):
    (repo / "a.py").write_text("a = 2\n")
    (repo / "b.py").write_text("b = 2\n")
    (repo / "new.py").write_text("n = 1\n")
    _git(repo, "add", "b.py")  # someone else's staged change: must stay out of this commit

    result = await git_commit("Change a and add new\n\nBecause.", ["a.py", "new.py"], branch="feat/a",
                              context=_ctx(repo))

    assert result.startswith("Committed on feat/a:")
    assert _git(repo, "branch", "--show-current").strip() == "feat/a"
    assert sorted(_git(repo, "show", "--name-only", "--format=", "HEAD").split()) == ["a.py", "new.py"]
    assert _git(repo, "log", "-1", "--format=%B").strip() == "Change a and add new\n\nBecause."
    assert _git(repo, "diff", "--cached", "--name-only").split() == ["b.py"]  # still staged, not committed
    assert _git(repo, "rev-parse", "main").strip() == _git(repo, "rev-parse", "HEAD~1").strip()


@pytest.mark.parametrize("name", [".env", "config/.env.local", "id_rsa", "server.pem", "credentials.json"])
async def test_never_commits_secrets(repo, name):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text("SECRET=1\n")
    result = await git_commit("Add config", [name], branch="feat/x", context=_ctx(repo))
    assert "looks like a secret" in result


async def test_a_folder_with_a_secret_inside_is_refused(repo):
    (repo / "conf").mkdir()
    (repo / "conf" / "app.toml").write_text("x = 1\n")
    (repo / "conf" / ".env").write_text("TOKEN=1\n")
    _git(repo, "switch", "-qc", "feat/conf")
    result = await git_commit("Add conf", ["conf"], context=_ctx(repo))
    assert "conf/.env looks like a secret" in result
    assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1"


@pytest.mark.parametrize("paths, error", [
    (["."], "staging the whole workspace"),
    (["../outside.py"], "outside the workspace"),
    (["missing.py"], "doesn't exist and isn't tracked"),
    ([], "paths is empty"),
])
async def test_rejects_bad_paths(repo, paths, error):
    _git(repo, "switch", "-qc", "feat/p")
    assert error in await git_commit("Msg", paths, context=_ctx(repo))


async def test_a_deleted_file_can_be_committed(repo):
    _git(repo, "switch", "-qc", "feat/rm")
    (repo / "b.py").unlink()
    result = await git_commit("Remove b", ["b.py"], context=_ctx(repo))
    assert result.startswith("Committed on feat/rm:")
    assert "b.py" not in _git(repo, "ls-files")


async def test_message_rules_and_nothing_to_commit(repo):
    _git(repo, "switch", "-qc", "feat/m")
    assert (await git_commit("", ["a.py"], context=_ctx(repo))).startswith("Error: message is empty")
    assert "at most" in await git_commit("x" * 101, ["a.py"], context=_ctx(repo))
    assert (await git_commit("No change", ["a.py"], context=_ctx(repo))).startswith("Nothing to commit")


async def test_a_failing_hook_commits_nothing(repo):
    _git(repo, "switch", "-qc", "feat/hook")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'lint failed: a.py'\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    (repo / "a.py").write_text("a = 3\n")
    result = await git_commit("Change a", ["a.py"], context=_ctx(repo))
    assert "nothing was committed" in result and "lint failed: a.py" in result
    assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1"


async def test_an_existing_branch_is_not_reused(repo):
    _git(repo, "branch", "feat/taken")
    (repo / "a.py").write_text("a = 2\n")
    assert "already exists" in await git_commit("Change a", ["a.py"], branch="feat/taken", context=_ctx(repo))


# --- pull requests ---------------------------------------------------------------------------


async def test_pull_request_without_gh_says_so(repo, monkeypatch):
    monkeypatch.setattr(git_write.shutil, "which", lambda _name: None)
    result = await create_pull_request("Title", "Body", context=_ctx(repo))
    assert "gh) isn't installed" in result


@pytest.fixture
def with_remote(repo, tmp_path_factory, monkeypatch):
    remote = tmp_path_factory.mktemp("remote") / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    bindir = tmp_path_factory.mktemp("bin")
    log = bindir / "gh.log"
    gh = bindir / "gh"
    gh.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\necho https://github.com/o/r/pull/7\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return remote, log


async def test_pull_request_pushes_the_branch_and_opens_it(repo, with_remote):
    remote, log = with_remote
    (repo / "a.py").write_text("a = 2\n")
    await git_commit("Change a", ["a.py"], branch="feat/pr", context=_ctx(repo))

    result = await create_pull_request("Change a", "Why and how tested", draft=True, context=_ctx(repo))

    assert result == "Pull request opened: https://github.com/o/r/pull/7"
    assert "feat/pr" in subprocess.run(["git", "branch"], cwd=remote, capture_output=True, text=True).stdout
    call = log.read_text()
    assert "pr create" in call and "--head feat/pr" in call and "--draft" in call


async def test_pull_request_refuses_the_default_branch_and_dirty_trees(repo, with_remote):
    assert "default branch" in await create_pull_request("T", "B", context=_ctx(repo))
    _git(repo, "switch", "-qc", "feat/dirty")
    (repo / "a.py").write_text("a = 9\n")
    assert "uncommitted changes" in await create_pull_request("T", "B", context=_ctx(repo))

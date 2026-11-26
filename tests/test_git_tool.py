"""Tests for the read-only git_diff tool."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.git import git_diff, git_status

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _run(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _run(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("first\n", encoding="utf-8")
    _run(tmp_path, "add", ".")
    _run(tmp_path, "commit", "-q", "-m", "initial commit")
    return tmp_path


@pytest.mark.asyncio
async def test_shows_uncommitted_changes_and_untracked_files(repo: Path):
    context = AgentContext(workspace=repo)
    (repo / "app.py").write_text("def total(items):\n    return sum(items) or 0\n", encoding="utf-8")
    (repo / "new_module.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = await git_diff(context=context)

    assert "-    return sum(items)" in result
    assert "+    return sum(items) or 0" in result
    assert "New untracked files" in result and "new_module.py" in result
    assert "notes.md" not in result


@pytest.mark.asyncio
async def test_includes_staged_changes_and_limits_to_paths(repo: Path):
    context = AgentContext(workspace=repo)
    (repo / "app.py").write_text("def total(items):\n    return 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("first\nsecond\n", encoding="utf-8")
    _run(repo, "add", "app.py")

    everything = await git_diff(context=context)
    only_notes = await git_diff(paths=["notes.md"], context=context)

    assert "+    return 1" in everything and "+second" in everything
    assert "+second" in only_notes and "app.py" not in only_notes


@pytest.mark.asyncio
async def test_stat_only_lists_files_without_the_diff_text(repo: Path):
    context = AgentContext(workspace=repo)
    (repo / "notes.md").write_text("first\nsecond\n", encoding="utf-8")

    result = await git_diff(stat_only=True, context=context)

    assert "notes.md" in result and "1 file changed" in result
    assert "+second" not in result


@pytest.mark.asyncio
async def test_compares_with_a_base_and_shows_a_single_commit(repo: Path):
    context = AgentContext(workspace=repo)
    _run(repo, "checkout", "-q", "-b", "feature")
    (repo / "notes.md").write_text("first\nfeature line\n", encoding="utf-8")
    _run(repo, "commit", "-q", "-am", "add feature line")

    against_main = await git_diff(base="main...HEAD", context=context)
    commit = await git_diff(commit="HEAD", context=context)

    assert "+feature line" in against_main
    assert "add feature line" in commit and "+feature line" in commit
    assert await git_diff(context=context) == "No changes (uncommitted)."


@pytest.mark.asyncio
async def test_rejects_anything_that_is_not_a_plain_ref(repo: Path):
    context = AgentContext(workspace=repo)
    marker = repo / "pwned"

    for bad in ("--output=pwned", "-p", "main; touch pwned", "$(touch pwned)", "main --output=pwned"):
        result = await git_diff(base=bad, context=context)
        assert result.startswith("Error: `base` must be"), bad
        assert (await git_diff(commit=bad, context=context)).startswith("Error: `commit` must be"), bad

    assert "not both" in await git_diff(base="main", commit="HEAD", context=context)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_path_that_looks_like_an_option_is_treated_as_a_path(repo: Path):
    context = AgentContext(workspace=repo)
    (repo / "notes.md").write_text("first\nsecond\n", encoding="utf-8")

    result = await git_diff(paths=["--output=pwned"], context=context)

    assert result == "No changes (uncommitted)."
    assert not (repo / "pwned").exists()


@pytest.mark.asyncio
async def test_leaves_the_repository_untouched(repo: Path):
    context = AgentContext(workspace=repo)
    (repo / "app.py").write_text("changed\n", encoding="utf-8")
    before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout

    await git_diff(context=context)
    await git_diff(base="main", stat_only=True, context=context)

    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert before == after == " M app.py\n"


@pytest.mark.asyncio
async def test_reports_when_the_workspace_is_not_a_repository(tmp_path: Path):
    result = await git_diff(context=AgentContext(workspace=tmp_path))

    assert "is not inside a Git repository" in result


@pytest.mark.asyncio
async def test_long_diffs_are_clipped_with_a_hint(repo: Path):
    context = AgentContext(workspace=repo)
    (repo / "notes.md").write_text("\n".join(f"line {i} " + "x" * 80 for i in range(600)) + "\n", encoding="utf-8")

    result = await git_diff(context=context)

    assert len(result) < 19_000
    assert "more characters not shown" in result and "`paths`" in result


@pytest.mark.asyncio
async def test_git_status_lists_branch_and_changes(repo: Path):
    context = AgentContext(workspace=repo)
    assert (await git_status(context=context)).endswith("Working tree clean.")
    (repo / "app.py").write_text("changed\n", encoding="utf-8")
    (repo / "new.py").write_text("x\n", encoding="utf-8")

    result = await git_status(context=context)
    only_new = await git_status(paths=["new.py"], context=context)

    assert result.splitlines()[0].startswith("## main")
    assert " M app.py" in result and "?? new.py" in result
    assert "app.py" not in only_new and "?? new.py" in only_new


@pytest.mark.asyncio
async def test_git_status_outside_a_repository(tmp_path: Path):
    result = await git_status(context=AgentContext(workspace=tmp_path))
    assert result.startswith("Error")

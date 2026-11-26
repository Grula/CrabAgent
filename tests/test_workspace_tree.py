"""Tests for the size-capped workspace tree in the system prompt."""
from __future__ import annotations

from pathlib import Path

from crabagent.core.agent.capability_manifest import build_runtime_system_prompt
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.workspace_tree import (
    WORKSPACE_SECTION_HEADING,
    build_workspace_section,
    build_workspace_tree,
)
from crabagent.core.config import settings


def _fixture(root: Path) -> Path:
    """A small tree: two dirs, a nested file, two root files."""
    (root / "src" / "core").mkdir(parents=True)
    (root / "src" / "core" / "loop.py").write_text("x")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("x")
    (root / "README.md").write_text("x")
    (root / "main.py").write_text("x")
    return root


def test_small_tree_is_deterministic_and_sorted(tmp_path):
    root = _fixture(tmp_path)
    expected = "docs/\n  guide.md\nsrc/\n  core/\n    loop.py\nREADME.md\nmain.py"
    assert build_workspace_tree(root) == expected
    assert build_workspace_tree(root) == expected


def test_directories_before_files_at_each_level(tmp_path):
    root = tmp_path
    (root / "zdir").mkdir()
    (root / "afile.txt").write_text("x")
    lines = build_workspace_tree(root).splitlines()
    assert lines == ["zdir/", "afile.txt"]


def test_ignore_list_honoured(tmp_path):
    root = tmp_path
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("x")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("x")
    (root / "build").mkdir()
    (root / "build" / "out.txt").write_text("x")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "secret").write_text("x")
    (root / ".env").write_text("x")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "m.pyc").write_text("x")
    (root / "app.py").write_text("x")

    out = build_workspace_tree(root)

    assert out == "app.py"
    for noise in ("node_modules", ".git", "build", ".hidden", ".env", "__pycache__"):
        assert noise not in out


def test_entry_cap_stops_and_reports_rest(tmp_path):
    root = tmp_path
    for i in range(10):
        (root / f"f{i}.txt").write_text("x")
    out = build_workspace_tree(root, max_entries=3)
    lines = out.splitlines()
    assert lines == ["f0.txt", "f1.txt", "f2.txt", "… (7 more entries not shown)"]


def test_char_cap_is_respected(tmp_path):
    root = tmp_path
    for i in range(60):
        (root / f"averylongfilenamethatfillsthebudget{i:02d}.txt").write_text("x")
    out = build_workspace_tree(root, max_entries=200, max_chars=300)
    assert 0 < len(out) <= 300
    assert out.splitlines()[-1].startswith("… (")
    assert out.splitlines()[-1].endswith("more entries not shown)")


def test_footer_counts_entries_trimmed_to_make_room_for_it(tmp_path):
    """Lines dropped to fit the footer are omitted entries too: the footer used
    to be formatted before that trimming, so it kept the smaller pre-trim count."""
    root = tmp_path
    for i in range(10):
        (root / f"f{i}.txt").write_text("x")

    out = build_workspace_tree(root, max_entries=100, max_chars=60)

    lines = out.splitlines()
    shown = len(lines) - 1  # the last line is the footer
    omitted = 10 - shown
    assert len(out) <= 60
    assert lines[-1] == f"… ({omitted} more entries not shown)"


def test_missing_workspace_returns_empty(tmp_path):
    assert build_workspace_tree(tmp_path / "does-not-exist") == ""
    assert build_workspace_section(tmp_path / "does-not-exist") == ""
    # A file, not a directory, is not a workspace.
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert build_workspace_tree(f) == ""


def test_zero_setting_removes_section(tmp_path, monkeypatch):
    root = _fixture(tmp_path)
    monkeypatch.setattr(settings, "workspace_tree_max_entries", 0)
    context = AgentContext(workspace=root, system_prompt="BASE")
    out = build_runtime_system_prompt(context)
    assert WORKSPACE_SECTION_HEADING not in out
    assert out.startswith("BASE")
    assert "README.md" not in out


def test_section_appears_once_for_a_real_run_context(tmp_path, monkeypatch):
    root = _fixture(tmp_path)
    monkeypatch.setattr(settings, "workspace_tree_max_entries", 200)
    context = AgentContext(workspace=root, system_prompt="BASE")

    out = build_runtime_system_prompt(context)
    assert out.count(WORKSPACE_SECTION_HEADING) == 1
    assert "src/\n  core/\n    loop.py" in out

    # A second pass over the already-built prompt must not duplicate it.
    context.system_prompt = out
    again = build_runtime_system_prompt(context)
    assert again.count(WORKSPACE_SECTION_HEADING) == 1


def test_default_entry_cap_is_small():
    assert settings.workspace_tree_max_entries == 200

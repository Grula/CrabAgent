"""Tests for skill discovery (core/agent/skill/loader.py)."""
from __future__ import annotations

from pathlib import Path

from crabagent.core.agent.skill.loader import discover_skills, parse_skill_md

SKILL_TEXT = "---\nname: demo-skill\ndescription: Does a demo. Use when testing.\n---\n\n# Demo\n\nBody.\n"


def _make_skill(root: Path) -> Path:
    skill_dir = root / "demo-skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "scripts" / "__pycache__").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    (skill_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")
    (skill_dir / "scripts" / "tool.py").write_text("print('hi')\n", encoding="utf-8")
    (skill_dir / "scripts" / "__pycache__" / "tool.cpython-312.pyc").write_bytes(b"\x00")
    (skill_dir / ".DS_Store").write_bytes(b"\x00")
    return skill_dir


def test_parse_skill_md_reads_frontmatter_and_body(tmp_path: Path):
    skill_dir = _make_skill(tmp_path)

    info = parse_skill_md(skill_dir / "SKILL.md")

    assert info is not None
    assert info.name == "demo-skill"
    assert info.description == "Does a demo. Use when testing."
    assert info.content.startswith("# Demo")


def test_auxiliary_files_skip_bytecode_caches_and_hidden_files(tmp_path: Path):
    skill_dir = _make_skill(tmp_path)

    info = parse_skill_md(skill_dir / "SKILL.md")

    assert info is not None
    names = sorted(str(p.relative_to(skill_dir)) for p in info.auxiliary_files)
    assert names == ["references/guide.md", "scripts/tool.py"]


def test_bundled_skills_all_load_within_the_auxiliary_file_limit():
    bundled = Path(__file__).resolve().parents[1] / "src" / "crabagent" / "skills"
    directories = sorted(p for p in bundled.iterdir() if p.is_dir())

    skills = discover_skills([bundled])

    # Every bundled skill directory must parse: kebab-case name equal to the
    # directory name and a one-line description.
    assert sorted(skills) == [p.name for p in directories]
    for name, info in skills.items():
        assert info.location.name == name
        assert "\n" not in info.description and 20 <= len(info.description) <= 1024
        real_files = [
            p
            for p in info.location.rglob("*")
            if p.is_file() and p.name != "SKILL.md" and "__pycache__" not in p.parts
        ]
        assert len(real_files) <= 10, f"{name} has more auxiliary files than the loader lists"


def test_bundled_skills_are_discovered_and_workspace_skills_override_them(tmp_path: Path, monkeypatch):
    from crabagent.core.config import Settings

    workspace_skills = tmp_path / ".crabagent" / "skills" / "code-review"
    workspace_skills.mkdir(parents=True)
    (workspace_skills / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Workspace override. Use for testing.\n---\n\nLocal body.\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    settings = Settings(workspace=tmp_path)

    dirs = settings.skill_discovery_dirs()
    skills = discover_skills(dirs)

    bundled = Path(__file__).resolve().parents[1] / "src" / "crabagent" / "skills"
    assert dirs[0] == bundled
    assert "deep-research" in skills  # bundled skill, no workspace copy needed
    assert skills["code-review"].description == "Workspace override. Use for testing."

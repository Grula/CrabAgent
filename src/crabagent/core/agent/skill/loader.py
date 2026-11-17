from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_MAX_AUX_FILES = 10


@dataclass
class SkillInfo:
    name: str
    description: str
    content: str
    location: Path
    auxiliary_files: list[Path] = field(default_factory=list)


def parse_skill_md(path: Path) -> SkillInfo | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None

    frontmatter = match.group(1)
    body = text[match.end() :]

    name = ""
    description = ""
    for line in frontmatter.splitlines():
        line = line.strip()
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("\"'")
        elif line.startswith("description:"):
            description = line.split(":", 1)[1].strip().strip("\"'")

    if not name or not _SKILL_NAME_RE.match(name):
        return None
    if not description:
        return None
    if len(name) > 64:
        return None
    if len(description) > 1024:
        description = description[:1024]

    skill_dir = path.parent
    aux_files = []
    try:
        for child in sorted(skill_dir.rglob("*")):
            if not child.is_file() or child.name == "SKILL.md":
                continue
            # Bytecode caches and hidden files are not skill content; counting
            # them would crowd real files out of the auxiliary-file limit.
            rel_parts = child.relative_to(skill_dir).parts
            if "__pycache__" in rel_parts or child.suffix == ".pyc" or any(p.startswith(".") for p in rel_parts):
                continue
            aux_files.append(child)
            if len(aux_files) >= _MAX_AUX_FILES:
                break
    except Exception:
        pass

    return SkillInfo(
        name=name,
        description=description,
        content=body.strip(),
        location=skill_dir,
        auxiliary_files=aux_files,
    )


def discover_skills(directories: list[Path]) -> dict[str, SkillInfo]:
    skills: dict[str, SkillInfo] = {}
    for directory in directories:
        if not directory.exists() or not directory.is_dir():
            continue
        try:
            for skill_md in directory.rglob("SKILL.md"):
                info = parse_skill_md(skill_md)
                if info:
                    skills[info.name] = info
        except Exception:
            continue
    return skills


def format_skill_content(skill: SkillInfo) -> str:
    parts = [
        f'<skill_content name="{skill.name}">\n',
        f"# Skill: {skill.name}\n\n",
        skill.content,
    ]
    if skill.auxiliary_files:
        parts.append("\n\nAuxiliary files (use read tool to load):")
        for f in skill.auxiliary_files:
            try:
                rel = f.relative_to(skill.location)
                parts.append(f"  - {rel}")
            except ValueError:
                parts.append(f"  - {f}")
    parts.append(f"\n\nSkill directory: {skill.location}")
    parts.append("\n</skill_content>")
    return "\n".join(parts)


def _skill_tool_info(own: dict[str, SkillInfo], every: dict[str, SkillInfo]):
    """Build the ``skill`` tool offering only ``own``.

    ``every`` (all discovered skills) rides along in the tool metadata so a
    narrower copy can be cut for another agent later.
    """
    from crabagent.core.agent.tools.registry import ToolInfo

    available_lines = "\n".join(
        f"  - **{s.name}**: {s.description}" for s in sorted(own.values(), key=lambda s: s.name)
    )
    description = (
        "Load a specialized skill that provides domain-specific instructions and workflows. "
        "Use this tool when you recognize that the current task matches one of the available skills. "
        "Returns the full skill instructions and auxiliary file references.\n\n"
        "Available skills:\n" + available_lines
    )

    def load_skill(name: str) -> str:
        skill = own.get(name)
        if not skill:
            names = ", ".join(sorted(own.keys()))
            return f"Error: skill '{name}' not found. Available skills: {names}"
        return format_skill_content(skill)

    return ToolInfo(
        name="skill",
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the skill to load from available skills",
                },
            },
            "required": ["name"],
        },
        handler=load_skill,
        metadata={"source": "builtin", "all_skills": every},
    )


def register_skill_tool(registry, skills: dict[str, SkillInfo]):
    if not skills:
        return
    registry._tools["skill"] = _skill_tool_info(skills, skills)


def scope_skill_tool(registry, allowed: list[str] | None) -> None:
    """Narrow ``registry``'s skill tool to one agent's own skills.

    ``allowed`` is the agent profile's ``skills`` list. ``None`` means the
    profile sets no list, so the agent keeps every skill. An empty list, or a
    list that names no installed skill, removes the tool. Only call this on a
    registry built for that agent; it replaces the tool in place.
    """
    if allowed is None:
        return
    tool = registry._tools.get("skill")
    if tool is None:
        return
    every: dict[str, SkillInfo] = (tool.metadata or {}).get("all_skills") or {}
    wanted = set(allowed)
    own = {name: info for name, info in every.items() if name in wanted}
    if own:
        registry._tools["skill"] = _skill_tool_info(own, every)
    else:
        registry._tools.pop("skill", None)

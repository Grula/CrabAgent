#!/usr/bin/env python3
"""Validate a CrabAgent skill folder against the rules of the skill loader.

Usage: python validate_skill.py <path/to/skill-folder> [more folders...]
Exit code 0 when every folder is valid, 1 otherwise. Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
MAX_AUX = 10
JUNK = {".DS_Store", "Thumbs.db"}


def check(folder: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        return [f"missing {skill_md}"], warnings

    text = skill_md.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return ["frontmatter must start on line 1 with '---' and close with '---' + newline"], warnings

    name = description = ""
    name_hits = desc_hits = 0
    lines = match.group(1).splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("name:"):
            name_hits += 1
            name = line.split(":", 1)[1].strip().strip("\"'")
        elif line.startswith("description:"):
            desc_hits += 1
            description = line.split(":", 1)[1].strip().strip("\"'")
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if description in {">", "|", ">-", "|-"} or (nxt.startswith((" ", "\t")) and nxt.strip()):
                errors.append("description must be a single line (no YAML folding or continuation lines)")

    if name_hits > 1:
        errors.append("more than one line starts with 'name:' in frontmatter; the last one wins")
    if desc_hits > 1:
        errors.append("more than one line starts with 'description:' in frontmatter; the last one wins")
    if not name:
        errors.append("missing 'name'")
    elif not NAME_RE.match(name):
        errors.append(f"name '{name}' must match ^[a-z0-9]+(-[a-z0-9]+)*$")
    elif len(name) > 64:
        errors.append("name longer than 64 characters")
    elif name != folder.resolve().name:
        errors.append(f"name '{name}' does not match folder name '{folder.resolve().name}'")
    if not description:
        errors.append("missing 'description'")
    elif len(description) > 1024:
        errors.append(f"description is {len(description)} chars; loader truncates at 1024")
    elif len(description) > 300:
        warnings.append(f"description is {len(description)} chars; aim for under ~300")

    aux = [p for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "SKILL.md"]
    if len(aux) > MAX_AUX:
        errors.append(f"{len(aux)} auxiliary files; only the first {MAX_AUX} are listed to the agent")
    for p in aux:
        if p.name in JUNK or "__pycache__" in p.parts:
            warnings.append(f"stray file counts toward the aux limit: {p.relative_to(folder)}")

    body_lines = len(text[match.end():].strip().splitlines())
    if body_lines > 250:
        warnings.append(f"SKILL.md body is {body_lines} lines; consider moving detail to references/")
    for ref in re.findall(r"\((references/[^)\s]+)\)|`(references/[^`\s]+)`", text):
        rel = ref[0] or ref[1]
        if "<" not in rel and not (folder / rel).exists():
            warnings.append(f"referenced file not found: {rel}")
    return errors, warnings


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    ok = True
    for arg in argv:
        folder = Path(arg)
        errors, warnings = check(folder)
        status = "OK" if not errors else "FAIL"
        print(f"[{status}] {folder}")
        for e in errors:
            print(f"  error: {e}")
        for w in warnings:
            print(f"  warning: {w}")
        ok = ok and not errors
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

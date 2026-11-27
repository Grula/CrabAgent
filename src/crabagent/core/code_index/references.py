"""Where a name is used: definitions, calls, imports and other references.

Answers "who calls X" in one call instead of 5-10 greps. Python files are
classified with ``ast`` (exact: a call is an ``ast.Call``, an import an import
statement); TypeScript/JavaScript with line patterns. Matching is by name: a
method ``execute`` also matches other objects' ``execute`` methods, since there
is no type information. Not an LSP, but it reads only the files that contain
the name, so it is fast enough to run on demand without an index.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from crabagent.core.code_index.extract import iter_files, read_source

CODE_LANGS = {"python", "typescript", "tsx", "javascript", "jsx"}
# Longer lines (generated constants, minified code) are skipped: the patterns
# backtrack quadratically on long identifier runs. Same cap as the indexer.
MAX_LINE_CHARS = 4000
KIND_ORDER = {"definition": 0, "call": 1, "import": 2, "reference": 3}
_MAX_TEXT = 160


@dataclass(frozen=True)
class Reference:
    path: str
    line: int
    kind: str  # definition | call | import | reference
    text: str
    # The expression before ".name" for attribute uses ("self.registry"), else "".
    receiver: str = ""


def _clip(text: str) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= _MAX_TEXT else compact[: _MAX_TEXT - 1] + "…"


def _python_refs(path: str, text: str, name: str) -> list[Reference]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return _pattern_refs(path, text, name)
    lines = text.splitlines()
    found: dict[tuple[int, str], str] = {}
    call_funcs: set[int] = set()

    def add(line: int, kind: str, receiver: str = "") -> None:
        found.setdefault((line, kind), receiver)

    def receiver_of(attribute: ast.Attribute) -> str:
        try:
            return ast.unparse(attribute.value)
        except Exception:
            return ""

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            add(node.lineno, "definition")
        elif isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == name) or (
                isinstance(func, ast.Attribute) and func.attr == name
            ):
                call_funcs.add(id(func))
                receiver = receiver_of(func) if isinstance(func, ast.Attribute) else ""
                add(getattr(func, "end_lineno", None) or node.lineno, "call", receiver)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == name or alias.name.endswith("." + name) or alias.asname == name:
                    add(node.lineno, "import")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                add(node.lineno, "definition")
    for node in ast.walk(tree):
        if id(node) in call_funcs:
            continue
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
            add(node.lineno, "reference")
        elif isinstance(node, ast.Attribute) and node.attr == name and isinstance(node.ctx, ast.Load):
            add(getattr(node, "end_lineno", None) or node.lineno, "reference", receiver_of(node))

    # One entry per line: the strongest kind wins (a call line isn't also a reference).
    best: dict[int, tuple[str, str]] = {}
    for (line, kind), receiver in found.items():
        if line not in best or KIND_ORDER[kind] < KIND_ORDER[best[line][0]]:
            best[line] = (kind, receiver)
    return [
        Reference(path, line, kind, _clip(lines[line - 1]) if 0 < line <= len(lines) else "", receiver)
        for line, (kind, receiver) in best.items()
    ]


def _pattern_refs(path: str, text: str, name: str) -> list[Reference]:
    word = re.escape(name)
    any_use = re.compile(rf"(?<![\w$]){word}(?![\w$])")
    definition = re.compile(
        rf"\b(?:function\*?|class|interface|type|enum|def)\s+{word}\b"
        rf"|\b(?:const|let|var)\s+{word}\s*[=:]"
        rf"|^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?{word}\s*\([^)]*\)\s*(?::[^={{]+)?\{{"
    )
    importing = re.compile(r"^\s*import\b|^\s*export\s+\{[^}]*\}\s+from\b|\brequire\(")
    call = re.compile(rf"(?<![\w$]){word}\s*(?:<[^<>()]*>)?\(|<{word}(?:[\s/>]|$)")
    receiver_re = re.compile(rf"([\w$.\]\)]+)\.{word}(?![\w$])")
    refs: list[Reference] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if len(line) > MAX_LINE_CHARS or not any_use.search(line):
            continue
        stripped = line.strip()
        if stripped.startswith(("//", "/*", "*", "#")):
            continue
        if definition.search(line):
            kind = "definition"
        elif importing.search(line):
            kind = "import"
        elif call.search(line):
            kind = "call"
        else:
            kind = "reference"
        receiver = receiver_re.search(line)
        refs.append(Reference(path, number, kind, _clip(line), receiver.group(1) if receiver else ""))
    return refs


def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith(("tests/", "test/"))
        or "/tests/" in path
        or "/__tests__/" in path
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


def _qualifier_words(qualifier: str) -> list[str]:
    """``ToolRegistry`` -> ``["toolregistry", "tool", "registry"]`` (words of 4+ letters)."""
    words = [qualifier.lower()]
    words += [w.lower() for w in re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", qualifier) if len(w) >= 4]
    return list(dict.fromkeys(words))


def narrow_to_qualifier(refs: list[Reference], qualifier: str) -> tuple[list[Reference], int]:
    """For ``Class.method``: keep attribute uses whose receiver mentions the class.

    Without types, ``db.execute`` and ``registry.execute`` look alike; a receiver
    named after the class (``self.registry``, ``tool_registry``) is the best cheap
    signal. Definitions, imports and bare-name uses are kept. Returns the kept
    references and how many were hidden; nothing is hidden when no receiver matches.
    """
    words = _qualifier_words(qualifier)
    kept = [
        r for r in refs
        if not r.receiver or any(word in r.receiver.lower() for word in words)
    ]
    if not any(r.receiver for r in kept):
        return refs, 0
    return kept, len(refs) - len(kept)


def sort_key(ref: Reference) -> tuple:
    """Definitions, calls, imports, other uses; production code before tests; then by location."""
    return KIND_ORDER[ref.kind], _is_test_path(ref.path), ref.path.count("/"), ref.path, ref.line


def find_references(workspace: Path, name: str, path_prefix: str | None = None) -> list[Reference]:
    """Every use of ``name`` in the workspace's code files: definitions, calls,
    imports, other references; production code before tests, then by location.

    Blocking (reads files): call it through ``asyncio.to_thread`` from async code.
    """
    prefix = (path_prefix or "").strip().removeprefix("./")
    word = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    refs: list[Reference] = []
    for scanned in iter_files(workspace):
        if scanned.lang not in CODE_LANGS or (prefix and not scanned.path.startswith(prefix)):
            continue
        source = read_source(Path(workspace) / scanned.path)
        if source is None or name not in source.text or not word.search(source.text):
            continue
        if scanned.lang == "python":
            refs.extend(_python_refs(scanned.path, source.text, name))
        else:
            refs.extend(_pattern_refs(scanned.path, source.text, name))
    refs.sort(key=sort_key)
    return refs


def format_references(
    refs: list[Reference], name: str, limit: int, hidden: int = 0, qualifier: str = "", note: str = ""
) -> str:
    if not refs:
        return f"No references to '{name}' in code files. Try grep for strings, configs or docs."
    counts = {kind: sum(1 for r in refs if r.kind == kind) for kind in KIND_ORDER}
    summary = ", ".join(f"{count} {kind}{'s' if count != 1 else ''}" for kind, count in counts.items() if count)
    shown = refs[:limit]
    header = f"'{name}': {summary}"
    if len(refs) > len(shown):
        header += f" ({len(shown)} of {len(refs)} shown; raise limit for more)"
    if hidden:
        header += (
            f". {hidden} uses on receivers not named like {qualifier} were left out "
            f"(matching is by name); search '{name}' alone to see them"
        )
    if note:
        header += f" {note}"
    out = [header]
    out += [f"{r.kind:<10} {r.path}:{r.line}  {r.text}" for r in shown]
    return "\n".join(out)


@dataclass(frozen=True)
class Definition:
    path: str  # workspace-relative
    line: int  # 1-based
    column: int  # 0-based, where the name starts
    qualname: str  # Class.method, function, or Outer.Inner


def _definitions_in(path: str, text: str, name: str) -> list[Definition]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    lines = text.splitlines()
    found: list[Definition] = []

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if child.name == name and 0 < child.lineno <= len(lines):
                    source = lines[child.lineno - 1]
                    column = source.find(name, child.col_offset)
                    if column >= 0:
                        found.append(Definition(path, child.lineno, column, ".".join([*scope, name])))
                # Methods are qualified by their class; a nested function by its parent.
                visit(child, [*scope, child.name])
            else:
                visit(child, scope)

    visit(tree, [])
    return found


def python_definitions(workspace: Path, name: str, qualifier: str = "") -> list[Definition]:
    """Python functions, methods and classes called ``name`` (``qualifier`` narrows to ``Class.name``).

    Blocking: call it through ``asyncio.to_thread``.
    """
    word = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    found: list[Definition] = []
    for scanned in iter_files(workspace):
        if scanned.lang != "python":
            continue
        source = read_source(Path(workspace) / scanned.path)
        if source is None or not word.search(source.text):
            continue
        found.extend(_definitions_in(scanned.path, source.text, name))
    if qualifier:
        found = [d for d in found if d.qualname.endswith(f"{qualifier}.{name}")]
    return found

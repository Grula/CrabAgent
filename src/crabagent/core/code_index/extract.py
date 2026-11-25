"""Turn workspace files into index rows: symbols plus text chunks.

No new dependency: Python is parsed with :mod:`ast`, TypeScript/JavaScript and
Markdown with regular expressions (documented as heuristics in their docstrings).

The walk reuses the workspace tree's ignore rule
(:func:`crabagent.core.agent.workspace_tree.skip_entry`), so build output,
dependency and cache directories and every hidden directory are skipped once,
in one place. ``static/assets`` holds the generated frontend bundle and is
skipped on top of that rule.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import stat as stat_module
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from crabagent.core.agent.workspace_tree import skip_entry

# Extensions we index, mapped to the language tag stored in ``code_files.lang``.
LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".md": "markdown",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".sh": "shell",
    ".sql": "sql",
}
ALLOWED_EXTENSIONS = frozenset(LANG_BY_EXT)

# Generated bundles: large, rewritten on every frontend build, never a source hit.
EXTRA_SKIP_RELATIVE_DIRS = ("static/assets",)

MAX_FILE_BYTES = 512 * 1024
NUL_SCAN_BYTES = 4096
CHUNK_LINES = 60
CHUNK_OVERLAP = 10
DOC_EXCERPT_CHARS = 200

# Symbol kinds: function, method, class, constant, interface, type, enum, heading.
SYMBOL_KINDS = frozenset({
    "function",
    "method",
    "class",
    "constant",
    "interface",
    "type",
    "enum",
    "heading",
})


@dataclass(frozen=True, slots=True)
class ScannedFile:
    """One indexable file found by :func:`iter_files`."""

    path: str  # workspace-relative, POSIX separators
    lang: str
    mtime_ms: int
    size: int


@dataclass(frozen=True, slots=True)
class Symbol:
    kind: str
    name: str
    qualname: str  # dotted, e.g. ``ToolRegistry.execute``
    start_line: int
    end_line: int
    signature: str  # the declaration's first source line, clipped
    doc: str  # first line of the docstring/comment, clipped


@dataclass(frozen=True, slots=True)
class Chunk:
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True, slots=True)
class Extracted:
    symbols: tuple[Symbol, ...] = ()
    chunks: tuple[Chunk, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceFile:
    text: str
    sha1: str


def _clip(text: str, limit: int = DOC_EXCERPT_CHARS) -> str:
    text = " ".join(text.split())
    return text[:limit]


# ── walking ────────────────────────────────────────────────────────────────


def _skip_dir(rel_dir: Path, name: str) -> bool:
    if skip_entry(name, is_dir=True):
        return True
    rel = (rel_dir / name).as_posix()
    return rel.endswith(EXTRA_SKIP_RELATIVE_DIRS)


def iter_files(workspace: Path) -> Iterator[ScannedFile]:
    """Yield every indexable file under ``workspace``, sorted and deterministic.

    Hidden directories, the workspace tree's noise directories and
    ``static/assets`` are pruned; files outside :data:`ALLOWED_EXTENSIONS` and
    files over :data:`MAX_FILE_BYTES` are dropped. Unreadable entries are
    skipped without raising.
    """
    root = Path(workspace)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(name for name in dirnames if not _skip_dir(rel_dir, name))
        for name in sorted(filenames):
            lang = LANG_BY_EXT.get(os.path.splitext(name)[1].lower())
            if lang is None:
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            if not stat_module.S_ISREG(st.st_mode):
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            yield ScannedFile(
                path=(rel_dir / name).as_posix(),
                lang=lang,
                mtime_ms=st.st_mtime_ns // 1_000_000,
                size=st.st_size,
            )


def read_source(path: Path) -> SourceFile | None:
    """Read a file for indexing, or ``None`` if it is binary, too large or unreadable.

    A NUL byte in the first :data:`NUL_SCAN_BYTES` marks it binary. Never raises.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(NUL_SCAN_BYTES)
            if b"\x00" in head:
                return None
            data = head + fh.read(MAX_FILE_BYTES + 1 - len(head))
    except OSError:
        return None
    if len(data) > MAX_FILE_BYTES:
        return None
    return SourceFile(text=data.decode("utf-8", errors="replace"), sha1=hashlib.sha1(data).hexdigest())


# ── text chunks ────────────────────────────────────────────────────────────


def chunk_text(
    text: str,
    *,
    size: int = CHUNK_LINES,
    overlap: int = CHUNK_OVERLAP,
) -> list[Chunk]:
    """Split ``text`` into ``size``-line chunks that overlap by ``overlap`` lines.

    Defaults are the design's 60/10: lines 1-60, 51-110, 101-160, ... with the
    final chunk truncated at the last line. Line numbers are 1-based inclusive.
    """
    lines = text.splitlines()
    if not lines:
        return []
    step = max(1, size - overlap)
    chunks: list[Chunk] = []
    start = 0
    while True:
        end = min(start + size, len(lines))
        chunks.append(Chunk(start_line=start + 1, end_line=end, text="\n".join(lines[start:end])))
        if end >= len(lines):
            return chunks
        start += step


# ── Python ─────────────────────────────────────────────────────────────────


def _py_start_line(node: ast.AST) -> int:
    """First line of a definition, decorators included."""
    lineno = getattr(node, "lineno", 1)
    decorators = getattr(node, "decorator_list", None) or []
    return min([lineno] + [d.lineno for d in decorators])


def _py_doc(node: ast.AST) -> str:
    doc = ast.get_docstring(node, clean=True) if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ) else None
    if not doc:
        return ""
    first = next((line for line in doc.splitlines() if line.strip()), "")
    return _clip(first)


def _py_body_walk(
    body: list[ast.stmt],
    prefix: str,
    in_class: bool,
    lines: list[str],
    out: list[Symbol],
) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}.{node.name}" if prefix else node.name
            lineno = getattr(node, "lineno", 1)
            out.append(Symbol(
                kind="method" if in_class else "function",
                name=node.name,
                qualname=qualname,
                start_line=_py_start_line(node),
                end_line=getattr(node, "end_lineno", None) or lineno,
                signature=_source_line(lines, lineno),
                doc=_py_doc(node),
            ))
            # Nested functions and classes: keep the dotted path, and stop
            # calling functions "methods" once inside a body.
            _py_body_walk(node.body, qualname, False, lines, out)
        elif isinstance(node, ast.ClassDef):
            qualname = f"{prefix}.{node.name}" if prefix else node.name
            lineno = getattr(node, "lineno", 1)
            out.append(Symbol(
                kind="class",
                name=node.name,
                qualname=qualname,
                start_line=_py_start_line(node),
                end_line=getattr(node, "end_lineno", None) or lineno,
                signature=_source_line(lines, lineno),
                doc=_py_doc(node),
            ))
            _py_body_walk(node.body, qualname, True, lines, out)
        elif not prefix and isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) and node.targets else getattr(
                node, "target", None
            )
            if isinstance(target, ast.Name):
                lineno = getattr(node, "lineno", 1)
                out.append(Symbol(
                    kind="constant",
                    name=target.id,
                    qualname=target.id,
                    start_line=lineno,
                    end_line=getattr(node, "end_lineno", None) or lineno,
                    signature=_source_line(lines, lineno),
                    doc="",
                ))


def _source_line(lines: list[str], lineno: int) -> str:
    if 1 <= lineno <= len(lines):
        return _clip(lines[lineno - 1].strip())
    return ""


def parse_python(text: str) -> list[Symbol]:
    """Extract functions, methods, classes and module-level constants with :mod:`ast`.

    Functions and classes nested anywhere are reported with their dotted
    ``qualname``; a definition directly inside a class is kind ``method``, any
    other function is kind ``function``. ``signature`` is the declaration's
    first source line, so a signature split over several lines is truncated
    there. A file that does not parse yields no symbols (it is still chunked).
    """
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    out: list[Symbol] = []
    _py_body_walk(tree.body, "", False, lines, out)
    return out


# ── TypeScript / JavaScript ────────────────────────────────────────────────

_IDENT = r"[A-Za-z_$][\w$]*"
_TS_MODIFIERS = (
    r"(?:(?:export|default|declare|abstract|public|private|protected|static"
    r"|async|readonly|override|get|set)\s+)*"
)

# Ordered: the first pattern that matches a line wins.
_TS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"^\s*{_TS_MODIFIERS}class\s+(?P<name>{_IDENT})"), "class"),
    (re.compile(rf"^\s*{_TS_MODIFIERS}function\s*\*?\s*(?P<name>{_IDENT})"), "function"),
    (re.compile(rf"^\s*{_TS_MODIFIERS}interface\s+(?P<name>{_IDENT})"), "interface"),
    (re.compile(rf"^\s*{_TS_MODIFIERS}enum\s+(?P<name>{_IDENT})"), "enum"),
    (re.compile(rf"^\s*{_TS_MODIFIERS}type\s+(?P<name>{_IDENT})\s*[=<]"), "type"),
    # const/let/var x = (...) => / x = arg => / x = function () {} / x = async () => {}
    (re.compile(
        rf"^\s*{_TS_MODIFIERS}(?:const|let|var)\s+(?P<name>{_IDENT})\s*"
        r"(?::[^=]+)?=\s*(?:async\s*)?(?:\([^()]*\)|[A-Za-z_$][\w$]*)\s*=>"
    ), "function"),
    (re.compile(
        rf"^\s*{_TS_MODIFIERS}(?:const|let|var)\s+(?P<name>{_IDENT})\s*(?::[^=]+)?=\s*(?:async\s+)?function\b"
    ), "function"),
    # Indented "name(args) {" / "name(args): ReturnType {" inside a class body.
    (re.compile(
        rf"^\s+{_TS_MODIFIERS}(?P<name>{_IDENT})\s*\([^()]*\)\s*(?::[^;{{}}]+?)?\s*\{{(?:\}}\s*)?$"
    ), "method"),
)

_TS_NOT_METHODS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "function", "do", "else", "try", "with", "typeof",
})
_TS_MAX_BLOCK_SCAN = 200


def _block_end(lines: list[str], start_idx: int) -> int:
    """Brace-match from ``start_idx`` (0-based) to the closing line, 1-based.

    A heuristic: braces inside strings or JSX can throw the count off, and a
    declaration without a ``{`` on its own line (``type X = string;``) is a
    single line. The scan is capped at :data:`_TS_MAX_BLOCK_SCAN` lines and
    falls back to the declaration line itself.
    """
    if "{" not in lines[start_idx]:
        return start_idx + 1
    depth = 0
    seen = False
    for idx in range(start_idx, min(len(lines), start_idx + _TS_MAX_BLOCK_SCAN)):
        for char in lines[idx]:
            if char == "{":
                depth += 1
                seen = True
            elif char == "}":
                depth -= 1
        if seen and depth <= 0:
            return idx + 1
    return start_idx + 1


def parse_typescript(text: str) -> list[Symbol]:
    """Extract symbols from TypeScript/JavaScript with a regex pass.

    This is a **heuristic**, not a parser: it recognises ``class``, ``function``,
    ``interface``, ``enum``, ``type``, ``const x = (...) =>`` / ``= function``
    (with exported variants) and indented method bodies in a class, and derives
    the end line by naive brace matching. It will miss shapes the regexes do not
    cover — notably an arrow function whose parameters span several lines, and a
    function passed as an object property. Those gaps are pinned by tests so
    they cannot change silently.
    """
    lines = text.splitlines()
    out: list[Symbol] = []
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        for pattern, kind in _TS_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            name = match.group("name")
            if kind == "method" and name in _TS_NOT_METHODS:
                continue
            out.append(Symbol(
                kind=kind,
                name=name,
                qualname=name,
                start_line=idx + 1,
                end_line=_block_end(lines, idx),
                signature=_clip(line.strip()),
                doc="",
            ))
            break

    # Qualify methods with the innermost enclosing class, when the brace match
    # found one.
    classes = [symbol for symbol in out if symbol.kind == "class"]
    qualified: list[Symbol] = []
    for symbol in out:
        if symbol.kind == "method" and classes:
            enclosing = [c for c in classes if c.start_line < symbol.start_line <= c.end_line]
            if enclosing:
                owner = max(enclosing, key=lambda c: c.start_line)
                symbol = Symbol(
                    kind=symbol.kind,
                    name=symbol.name,
                    qualname=f"{owner.name}.{symbol.name}",
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    signature=symbol.signature,
                    doc=symbol.doc,
                )
        qualified.append(symbol)
    return qualified


# ── Markdown ───────────────────────────────────────────────────────────────

_MD_HEADING = re.compile(r"^(#{1,6})\s+(?P<text>.+?)\s*#*\s*$")


def parse_markdown(text: str) -> list[Symbol]:
    """Markdown ATX headings (``#``..``######``) as kind ``heading``.

    ``name`` is the heading text, ``signature`` the whole line and ``end_line``
    the heading line itself (a heading's section length is not tracked). Lines
    are matched one by one, so a ``#`` comment inside a fenced code block is
    reported as a heading too.
    """
    out: list[Symbol] = []
    for idx, line in enumerate(text.splitlines()):
        match = _MD_HEADING.match(line)
        if not match:
            continue
        name = _clip(match.group("text"))
        out.append(Symbol(
            kind="heading",
            name=name,
            qualname=name,
            start_line=idx + 1,
            end_line=idx + 1,
            signature=_clip(line.strip()),
            doc="",
        ))
    return out


# ── entry point ────────────────────────────────────────────────────────────

_SYMBOL_PARSERS = {
    "python": parse_python,
    "typescript": parse_typescript,
    "tsx": parse_typescript,
    "javascript": parse_typescript,
    "jsx": parse_typescript,
    "markdown": parse_markdown,
}


def extract(lang: str, text: str) -> Extracted:
    """Symbols (when the language has a parser) plus 60/10 text chunks."""
    parser = _SYMBOL_PARSERS.get(lang)
    symbols = tuple(parser(text)) if parser else ()
    return Extracted(symbols=symbols, chunks=tuple(chunk_text(text)))

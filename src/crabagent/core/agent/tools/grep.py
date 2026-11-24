from __future__ import annotations

import fnmatch
import os
import re
from typing import Any

from crabagent.core.agent.tools.path_utils import resolve_tool_path
from crabagent.core.agent.tools.registry import registry

# ── Default directories to skip when searching ──────────────────────────
_DEFAULT_IGNORE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".npm",
    ".cargo",
    "Library",
    ".venv",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".egg-info",
    ".eggs",
    "site-packages",
    "molts",
    ".opencode",
    "venv",
}

# ── Hard limit on how many files to open before aborting ────────────────
_MAX_FILE_SCAN = 10_000

# ── Skip files larger than this (avoid reading huge binaries/logs) ──────
_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

# ── Stop after this many matching lines ────────────────────────────────
_MAX_MATCHES = 200


def _expand_braces(pattern: str) -> list[str]:
    """Expand shell-style brace patterns like ``*.{ts,js}`` into a list
    of plain patterns: ``["*.ts", "*.js"]``.

    ``fnmatch`` does not support ``{a,b}`` syntax, so we expand it
    ourselves before calling ``fnmatch``.
    """
    # Handle at most one brace group per pattern (covers the vast majority
    # of real-world cases like *.{py,pyi} or *.{ts,tsx,js}).
    m = re.search(r"\{([^}]+)\}", pattern)
    if not m:
        return [pattern]
    prefix = pattern[: m.start()]
    suffix = pattern[m.end() :]
    options = m.group(1).split(",")
    return [f"{prefix}{opt}{suffix}" for opt in options]


def _compile_path_glob(pattern: str) -> re.Pattern[str]:
    """Compile a directory-scoped include glob into a regex over relative paths.

    ``**`` spans any number of directories (``src/**/*.py`` matches both
    ``src/a.py`` and ``src/pkg/a.py``), ``*`` stays within one path segment
    and ``?`` matches one non-``/`` character. A leading ``./`` is ignored.
    """
    if pattern.startswith("./"):
        pattern = pattern[2:]
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*" and pattern.startswith("**", i):
            i += 2
            if i < len(pattern) and pattern[i] == "/":
                # "**/" = zero or more whole directory segments.
                out.append("(?:[^/]+/)*")
                i += 1
            else:
                out.append(".*")
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    flags = re.IGNORECASE if os.path.normcase("A") == os.path.normcase("a") else 0
    return re.compile("".join(out), flags)


class _IncludeMatcher:
    """Pre-compiled ``include`` filter.

    Patterns without ``/`` match the bare filename with plain ``fnmatch``
    semantics; patterns with ``/`` match the search-relative path (POSIX
    separators) using the ``**``-aware glob rules.
    """

    def __init__(self, patterns: list[str]) -> None:
        self._bare: list[str] = []
        self._paths: list[re.Pattern[str]] = []
        for pattern in patterns:
            if "/" in pattern:
                self._paths.append(_compile_path_glob(pattern))
            else:
                self._bare.append(pattern)

    def match(self, fname: str, rel_path: str) -> bool:
        """Whether *fname* or *rel_path* matches any include pattern."""
        for pattern in self._bare:
            if fnmatch.fnmatch(fname, pattern):
                return True
        for regex in self._paths:
            if regex.fullmatch(rel_path):
                return True
        return False


def _match_include(fname: str, patterns: list[str], rel_path: str | None = None) -> bool:
    """Check whether *fname* (or *rel_path*) matches any of the include patterns."""
    return _IncludeMatcher(patterns).match(fname, fname if rel_path is None else rel_path)


def _matches_with_context(
    fpath: str, regex: re.Pattern[str], context_lines: int, budget: int
) -> tuple[list[str], int]:
    """Search one file and return ``(output_lines, match_count)`` with surrounding context.

    Matching lines are formatted ``path:line: text`` and context lines
    ``path-line- text``; non-adjacent groups are separated by ``--`` (like
    ``grep -C``). At most *budget* matches are reported.
    """
    with open(fpath, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    hits = [i for i, line in enumerate(lines) if regex.search(line)][: max(0, budget)]
    if not hits:
        return [], 0
    hit_set = set(hits)
    output: list[str] = []
    last_shown = -1
    for hit in hits:
        start = max(0, hit - context_lines)
        end = min(len(lines) - 1, hit + context_lines)
        if output and start > last_shown + 1:
            output.append("--")
        for index in range(max(start, last_shown + 1), end + 1):
            sep = ":" if index in hit_set else "-"
            output.append(f"{fpath}{sep}{index + 1}{sep} {lines[index].rstrip()}")
        last_shown = max(last_shown, end)
    return output, len(hits)


def _is_binary(filepath: str) -> bool:
    """Quick heuristic: read first 4KB and check for NUL bytes."""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(4096)
        return b"\x00" in chunk
    except (OSError, PermissionError):
        return True  # if we can't even read it, treat as binary/skip


@registry.register(
    name="grep",
    description=(
        "Search file contents using regular expressions. "
        "Returns matching lines as path:line: text. "
        "Use this to search file contents; to find files by name or path, use glob. "
        "Set ignore_case for case-insensitive search and context_lines to see the lines around each match. "
        "Supports skipping common build/dependency directories and a "
        "configurable maximum search depth to avoid runaway traversals."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in. Default: current directory.",
            },
            "include": {
                "type": "string",
                "description": (
                    "Glob pattern matched against file paths to limit which files are searched "
                    '(not file content, not a regex): a bare filename glob ("*.py", '
                    '"*.{ts,tsx}") or a directory-scoped glob ("src/**/*.py", where ** spans '
                    "directories). Brace expansion is supported. The content search is pattern, "
                    "which is always a regular expression, not a glob."
                ),
            },
            "ignore_dirs": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Directory names to skip during recursive search. "
                    "Defaults to common build/dependency/cache directories "
                    "(.git, node_modules, __pycache__, .npm, .cargo, Library, "
                    ".venv, dist, build, .mypy_cache, .pytest_cache, .egg-info, "
                    "site-packages). Set to an empty list to disable skipping."
                ),
            },
            "max_depth": {
                "type": "integer",
                "description": ("Maximum directory recursion depth. Default: 15. Use -1 for unlimited."),
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Match letters regardless of case. Default: false.",
                "default": False,
            },
            "context_lines": {
                "type": "integer",
                "description": (
                    "Lines of context to show before and after each match (0-10). Context lines are "
                    "printed as path-line- text, and groups are separated by --. Default: 0."
                ),
                "default": 0,
            },
        },
        "required": ["pattern"],
    },
)
def grep_files(
    pattern: str,
    path: str = ".",
    include: str | None = None,
    ignore_dirs: list[str] | None = None,
    max_depth: int = 15,
    ignore_case: bool = False,
    context_lines: int = 0,
    context: Any = None,
) -> str:
    resolved_path, error = resolve_tool_path(path, context)
    if error:
        return error
    assert resolved_path is not None
    flags = re.IGNORECASE if ignore_case else 0
    try:
        context_lines = max(0, min(int(context_lines or 0), 10))
    except (TypeError, ValueError):
        context_lines = 0

    # ── Single-file mode: if *path* is a file, grep it directly ────────
    resolved = os.path.abspath(str(resolved_path))
    if os.path.isfile(resolved):
        try:
            regex_inner = re.compile(pattern, flags)
        except re.error as e:
            return f"Error: invalid regex: {e}"
        single_results: list[str] = []
        try:
            if os.path.getsize(resolved) > _MAX_FILE_SIZE or _is_binary(resolved):
                pass
            elif context_lines > 0:
                single_results, found = _matches_with_context(resolved, regex_inner, context_lines, _MAX_MATCHES)
                if found >= _MAX_MATCHES:
                    single_results.append("[Truncated: showing first 200 matches.]")
            else:
                with open(resolved, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if regex_inner.search(line):
                            single_results.append(f"{resolved}:{lineno}: {line.rstrip()}")
                            if len(single_results) >= 200:
                                single_results.append("[Truncated: showing first 200 matches.]")
                                break
        except (OSError, PermissionError):
            return f"Error: cannot read file: {resolved}"
        if not single_results:
            return "No matches found."
        return "\n".join(single_results)

    # ── Directory mode ──────────────────────────────────────────────────
    root_path = resolved
    if not os.path.isdir(root_path):
        return f"Error: path does not exist or is not a directory: {path}"

    # ── Compile pattern ─────────────────────────────────────────────────
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: invalid regex: {e}"

    # ── Normalise ignore set ────────────────────────────────────────────
    skip_dirs: set[str] = set(ignore_dirs) if ignore_dirs is not None else _DEFAULT_IGNORE_DIRS

    # ── Build include patterns (with brace expansion) ───────────────────
    matcher = _IncludeMatcher(_expand_braces(include)) if include else None

    results: list[str] = []
    match_count = 0
    scanned = 0

    # ── os.walk with pruning, depth tracking & size limit ───────────────
    # Compute the depth of root_path so we can measure relative depth.
    root_depth = root_path.rstrip(os.sep).count(os.sep)

    for current_root, dirs, files in os.walk(root_path, topdown=True, followlinks=False):
        # --- Depth guard ---
        if max_depth >= 0:
            current_depth = current_root.rstrip(os.sep).count(os.sep) - root_depth
            if current_depth > max_depth:
                # Do NOT descend further.
                dirs[:] = []
                continue

        # --- Prune ignored directories (mutate dirs in-place) ---
        # This prevents os.walk from ever entering them.
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        # --- Process files ---
        for fname in files:
            fpath = os.path.join(current_root, fname)
            if matcher is not None:
                rel_path = os.path.relpath(fpath, root_path).replace(os.sep, "/")
                if not matcher.match(fname, rel_path):
                    continue

            scanned += 1
            if scanned > _MAX_FILE_SCAN:
                results.append(
                    f"[TRUNCATED] Searched over {_MAX_FILE_SCAN:,} files — "
                    "scope too broad. Narrow your search with a more specific "
                    "path, pattern, or include filter."
                )
                # Flush what we have and stop.
                if results:
                    return "\n".join(results[:200])
                return (
                    f"Search scope too broad (>{_MAX_FILE_SCAN:,} files). "
                    "Please narrow your search with a more specific path, "
                    "pattern, or include filter."
                )

            try:
                # Skip oversized files and binary files
                if os.path.getsize(fpath) > _MAX_FILE_SIZE:
                    continue
                if _is_binary(fpath):
                    continue
                if context_lines > 0:
                    block, found = _matches_with_context(
                        fpath, regex, context_lines, _MAX_MATCHES - match_count
                    )
                    if found:
                        if results:
                            results.append("--")
                        results.extend(block)
                        match_count += found
                else:
                    with open(fpath, encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if regex.search(line):
                                results.append(f"{fpath}:{lineno}: {line.rstrip()}")
                                match_count += 1
                                if match_count >= _MAX_MATCHES:
                                    break
                if match_count >= _MAX_MATCHES:
                    break
            except Exception:
                continue

        if match_count >= _MAX_MATCHES:
            break

    if not results:
        return "No matches found."

    output = "\n".join(results if context_lines > 0 else results[:_MAX_MATCHES])
    if match_count >= _MAX_MATCHES:
        output += "\n[Truncated: showing first 200 matches. Refine your pattern or path for more precise results.]"
    return output

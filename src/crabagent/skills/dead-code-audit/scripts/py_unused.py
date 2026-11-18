"""List Python definitions and modules that nothing references. Read-only.

usage: py_unused.py --src <package dir> [--tests <dir> ...] [--also <dir or file> ...]

  --src    the top-level package, e.g. src/mypkg or mypkg
  --tests  test directories (default: tests, if it exists)
  --also   extra places that count as callers: scripts/, pyproject.toml, Dockerfile...

Prints three sections:
  UNREFERENCED  defined in --src, named nowhere else in --src, --also or the tests
  TESTS-ONLY    named only by tests: dead in production, the tests go with it
  MODULES       modules no other module imports (tests-only ones are marked)

These are candidates, not verdicts: a name can be reached through getattr, a
registry, a string, a decorator or a framework hook. Verify each one.
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import tokenize
from collections import Counter
from pathlib import Path

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Framework hooks and protocol methods that are called without being named.
HOOKS = {
    "model_post_init", "model_validate", "setUp", "tearDown", "setUpClass", "tearDownClass",
    "handle_starttag", "handle_endtag", "handle_data", "handle_startendtag", "handle_comment",
    "emit", "run", "visit", "generic_visit", "dispatch", "lifespan",
}


def name_counts(path: Path) -> Counter:
    """Every identifier in a file, including words inside strings (dynamic lookups)."""
    out: Counter = Counter()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    if path.suffix != ".py":
        out.update(WORD.findall(text))
        return out
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NAME:
                out[tok.string] += 1
            elif tok.type == tokenize.STRING:
                out.update(WORD.findall(tok.string))
    except (tokenize.TokenError, SyntaxError):
        out.update(WORD.findall(text))
    return out


def files_under(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for p in paths:
        if p.is_file():
            found.append(p)
        elif p.is_dir():
            found.extend(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts
                         and f.suffix in (".py", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".spec", ".sh", ""))
    return found


def definitions(path: Path):
    """(kind, name, line) for top-level defs/constants and class methods without decorators."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.decorator_list:
                yield "func", node.name, node.lineno
        elif isinstance(node, ast.ClassDef):
            yield "class", node.name, node.lineno
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name.startswith("__") or item.name in HOOKS:
                    continue
                plain = all(isinstance(d, ast.Name) and d.id in ("staticmethod", "classmethod")
                            for d in item.decorator_list)
                if plain:
                    yield "method", f"{node.name}.{item.name}", item.lineno
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and not t.id.startswith("__") and t.id not in ("logger", "router", "app"):
                    yield "const", t.id, node.lineno


def module_name(pkg: Path, path: Path) -> str:
    parts = list(path.relative_to(pkg.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_names(path: Path, this: str, is_pkg: bool) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = this.split(".") if is_pkg else this.split(".")[:-1]
                base = base[: len(base) - (n.level - 1)] if n.level > 1 else base
                mod = ".".join(base + ([n.module] if n.module else []))
            else:
                mod = n.module or ""
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in n.names)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and "." in n.value:
            out.add(n.value.split(":")[0])  # "pkg.mod:attr" entry points, importlib strings
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--tests", nargs="*", type=Path)
    ap.add_argument("--also", nargs="*", type=Path, default=[])
    args = ap.parse_args()
    pkg = args.src.resolve()
    tests = args.tests if args.tests is not None else [p for p in [Path("tests")] if p.is_dir()]
    src_files = sorted(p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts)
    test_files = files_under(tests)
    also_files = files_under(args.also)

    src_counts: Counter = Counter()
    for p in src_files + also_files:
        src_counts.update(name_counts(p))
    test_counts: Counter = Counter()
    for p in test_files:
        test_counts.update(name_counts(p))

    unreferenced, tests_only = [], []
    for p in src_files:
        for kind, name, line in definitions(p):
            short = name.split(".")[-1]
            if src_counts[short] - 1 > 0:
                continue
            row = f"{p}:{line}  {kind:6} {name}"
            (tests_only if test_counts[short] else unreferenced).append(row + (f"  (tests: {test_counts[short]})" if test_counts[short] else ""))

    # Modules: used when another module imports it, or a symbol from it (a submodule
    # import alone doesn't count as using the package's own code).
    def importable(p: Path) -> bool:
        # Scripts in plain folders (no __init__.py on the way down) are run, not imported.
        d = p.parent
        while True:
            if not (d / "__init__.py").exists():
                return False
            if d == pkg:
                return True
            d = d.parent

    mods = {module_name(pkg, p): p for p in src_files if importable(p)}
    users = {m: set() for m in mods}
    for m, p in mods.items():
        for name in imported_names(p, m, p.name == "__init__.py"):
            if name in mods and name != m:
                users[name].add(m)
            parent = name.rsplit(".", 1)[0]
            if "." in name and parent in mods and name not in mods and parent != m:
                users[parent].add(m)
    extra_text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in also_files)
    test_imports: set[str] = set()
    for p in test_files:
        if p.suffix == ".py":
            test_imports |= imported_names(p, "tests.x", False)
    def named_in_extras(m: str) -> bool:
        # by dotted name, or by file path (build specs and Dockerfiles name files)
        rel = mods[m].relative_to(pkg.parent).as_posix()
        if m in extra_text or (not rel.endswith("__init__.py") and rel in extra_text):
            return True
        name = mods[m].name
        return name != "__init__.py" and re.search(r"(?<![\w.])" + re.escape(name) + r"\b", extra_text) is not None

    used = {m for m, u in users.items() if u or named_in_extras(m)}
    # A package counts as used when any module inside it is used.
    for m in list(used):
        parts = m.split(".")
        for i in range(1, len(parts)):
            used.add(".".join(parts[:i]))
    module_rows = []
    for m in sorted(mods):
        if m in used or m.endswith("__main__") or m == pkg.name:
            continue
        in_tests = m in test_imports or any(t.startswith(m + ".") for t in test_imports)
        module_rows.append(f"{'TESTS-ONLY ' if in_tests else 'UNUSED     '}{m}  ({mods[m]})")

    for title, rows in (("UNREFERENCED", unreferenced), ("TESTS-ONLY", tests_only), ("MODULES", module_rows)):
        print(f"== {title} ({len(rows)})")
        for r in rows:
            print(r)
        print()


if __name__ == "__main__":
    main()

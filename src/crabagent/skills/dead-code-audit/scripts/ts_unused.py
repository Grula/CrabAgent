"""List TypeScript/JavaScript files nothing imports and exports nothing uses. Read-only.

usage: ts_unused.py <frontend dir> [--src src] [--entry src/main.tsx ...]

Resolves relative imports, the "@/" alias (to --src) and simple tsconfig "paths".
Entry points: files named by --entry, anything index.html or a vite/webpack
config mentions, and src/main.* / src/index.* when present.

Prints:
  FILES    no non-test importer (TEST-ONLY when only tests import it)
  EXPORTS  exported names used nowhere outside their own file
           (NO-USE: not used inside either; LOCAL: used only inside its file)

For unused locals and imports inside files, run the compiler instead:
  npx tsc --noEmit --noUnusedLocals -p <tsconfig>
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

EXTS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".mjs")
IMPORT_RE = re.compile(
    r"""(?:import|export)\s[^;]*?from\s+['"]([^'"]+)['"]"""
    r"""|import\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\s+['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|vi\.mock\(\s*['"]([^'"]+)['"]|jest\.mock\(\s*['"]([^'"]+)['"]""",
    re.S,
)
EXPORT_RE = re.compile(
    r"^export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?(?:function\*?|const|let|var|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)",
    re.M,
)


def is_test(p: Path) -> bool:
    return (".test." in p.name or ".spec." in p.name or "__tests__" in p.parts
            or p.name.startswith(("setupTests", "test-setup", "vitest.setup")))


def load_aliases(root: Path, src: Path) -> dict[str, Path]:
    aliases = {"@/": src}
    for name in ("tsconfig.json", "tsconfig.app.json"):
        cfg = root / name
        if not cfg.exists():
            continue
        try:
            text = re.sub(r"//.*|/\*[\s\S]*?\*/", "", cfg.read_text(encoding="utf-8"))
            data = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
        except (ValueError, OSError):
            continue
        opts = data.get("compilerOptions", {})
        base = root / opts.get("baseUrl", ".")
        for key, targets in (opts.get("paths") or {}).items():
            if key.endswith("/*") and targets:
                aliases[key[:-1]] = (base / targets[0].rstrip("*")).resolve()
    return aliases


def resolve(src_file: Path, spec: str, aliases: dict[str, Path], known: set[Path]) -> Path | None:
    base = None
    for prefix, target in aliases.items():
        if spec.startswith(prefix):
            base = target / spec[len(prefix):]
            break
    if base is None:
        if not spec.startswith("."):
            return None
        base = src_file.parent / spec
    base = Path(os.path.normpath(base))
    # Append extensions; never replace one (Foo.types must not become Foo.tsx).
    stem = re.sub(r"\.(js|jsx|mjs)$", "", str(base))
    candidates = [base] + [Path(stem + e) for e in EXTS] + [base / f"index{e}" for e in EXTS]
    for c in candidates:
        if c in known:
            return c
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--src", default="src")
    ap.add_argument("--entry", nargs="*", default=[])
    args = ap.parse_args()
    root = args.root.resolve()
    src = (root / args.src).resolve()
    files = sorted(p.resolve() for p in src.rglob("*") if p.suffix in EXTS and "node_modules" not in p.parts)
    known = set(files)
    text = {p: p.read_text(encoding="utf-8", errors="ignore") for p in files}
    aliases = load_aliases(root, src)

    importers: dict[Path, set[Path]] = {p: set() for p in files}
    for p, t in text.items():
        for m in IMPORT_RE.finditer(t):
            spec = next(g for g in m.groups() if g)
            r = resolve(p, spec, aliases, known)
            if r:
                importers[r].add(p)

    entries = {(root / e).resolve() for e in args.entry}
    entries |= {p for p in files if p.parent == src and p.stem in ("main", "index")}
    for cfg in ["index.html", *[c.name for c in root.glob("vite.config.*")], *[c.name for c in root.glob("webpack.config.*")]]:
        f = root / cfg
        if f.exists():
            for spec in re.findall(r"""['"](/?[\w./@-]+\.(?:t|j)sx?)['"]""", f.read_text(encoding="utf-8", errors="ignore")):
                entries.add((root / spec.lstrip("/")).resolve())

    print("== FILES")
    for p in files:
        if is_test(p) or p in entries or p.name.endswith(".d.ts"):
            continue
        prod = [x for x in importers[p] if not is_test(x)]
        if not prod:
            print(("TEST-ONLY " if importers[p] else "UNUSED    ") + str(p.relative_to(root)))

    print("\n== EXPORTS")
    prod_text = {p: t for p, t in text.items() if not is_test(p)}
    for p, t in text.items():
        if is_test(p):
            continue
        for m in EXPORT_RE.finditer(t):
            name = m.group(1)
            pat = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
            if any(pat.search(o) for q, o in prod_text.items() if q != p):
                continue
            inside = len(pat.findall(t)) > 1
            tests = sum(1 for q, o in text.items() if q != p and is_test(q) and pat.search(o))
            line = t[: m.start()].count("\n") + 1
            note = f"  (tests: {tests})" if tests else ""
            print(f"{'LOCAL ' if inside else 'NO-USE'} {p.relative_to(root)}:{line} {name}{note}")


if __name__ == "__main__":
    main()

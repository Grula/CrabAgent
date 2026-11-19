"""Run the tests and list the code they never execute. Standard library only.

usage: py_coverage.py --src <dir or file> [--src ...] [options] [-- <test command ...>]

  --src          code to measure, e.g. src/mypkg or mypkg (repeat for several)
  -- <command>   how the project runs its tests (default: <this python> -m pytest -q)
  --json FILE    also write the full result as JSON
  --all          list fully run files too
  --show-output  print the test command's whole output (default: its last lines)
  --timeout S    stop the test command after S seconds
  --tracer       auto (default: sys.monitoring on Python 3.12+), or settrace

Every Python process the command starts is measured: the test runner, the CLI or
server a test launches (even with its own env or PYTHONPATH), and a server a test
stops with SIGTERM. Not measured: processes started with `python -I` or `-S`,
killed with SIGKILL, or ended with os._exit() (forked multiprocessing workers).

Not counted as never run: `if __name__ == "__main__":` and `if TYPE_CHECKING:`
blocks, and lines or blocks marked `# pragma: no cover`.

Prints, per file with code that never ran: the functions that never ran and the
line ranges, then the files no test imported. Never-run code is a lead, not a
verdict: it may be reached only in production (a real network call, a platform
branch, an error path no test covers). Verify each item.

The exit code is the test command's.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

BOOT = Path(__file__).with_name("_coverage_boot.py")


def split_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


def source_files(srcs: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for src in srcs:
        if src.is_file():
            files.add(src.resolve())
        else:
            files.update(p.resolve() for p in src.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def executable_lines(code) -> set[int]:
    """Lines that carry bytecode, in this code object and every one nested in it."""
    lines: set[int] = set()
    stack = [code]
    while stack:
        co = stack.pop()
        # Line 0 is the interpreter's own start-up instruction, not a source line.
        lines.update(line for _, _, line in co.co_lines() if line)
        stack.extend(c for c in co.co_consts if hasattr(c, "co_lines"))
    return lines


def _is_main_guard(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    sides = [test.left, *test.comparators]
    return any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides) and any(
        isinstance(s, ast.Constant) and s.value == "__main__" for s in sides
    )


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def excluded_lines(tree: ast.AST, text: str) -> set[int]:
    """Lines that are not expected to run under tests (see the module docstring)."""
    pragma: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and "pragma: no cover" in tok.string:
                pragma.add(tok.start[0])
    except (tokenize.TokenError, SyntaxError):
        pass
    out: set[int] = set()
    covered_pragma: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        end = getattr(node, "end_lineno", None) or node.lineno
        if isinstance(node, ast.If) and (_is_main_guard(node.test) or _is_type_checking(node.test)):
            out.update(range(node.lineno, end + 1))
        first = node.lineno
        decorators = getattr(node, "decorator_list", None) or []
        if decorators:
            first = min(d.lineno for d in decorators)
        hit = pragma & set(range(first, node.lineno + 1))
        if hit:
            out.update(range(first, end + 1))
            covered_pragma.update(hit)
    out.update(pragma - covered_pragma)
    return out


def functions(tree: ast.AST):
    """(qualified name, def line, body start, end line) for every function, outermost first."""
    found = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = child.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body = body[1:]
                name = f"{prefix}{child.name}"
                if body:
                    found.append((name, child.lineno, body[0].lineno, child.end_lineno))
                visit(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def ranges(lines: list[int]) -> str:
    parts = []
    start = prev = None
    for line in lines:
        if start is None:
            start = prev = line
        elif line == prev + 1:
            prev = line
        else:
            parts.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = line
    if start is not None:
        parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def analyse(path: Path, run: set[int]) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, str(path))
        code = compile(tree, str(path), "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        return {"error": f"cannot parse: {exc}"}
    skip = excluded_lines(tree, text)
    lines = executable_lines(code) - skip
    never = sorted(lines - run)
    never_set = set(never)
    dead_functions = []
    inside: list[tuple[int, int]] = []
    for name, line, body_start, end in functions(tree):
        if any(a <= line <= b for a, b in inside):
            continue  # inside a function already reported
        body = {n for n in lines if body_start <= n <= end}
        if body and body <= never_set:
            dead_functions.append({"name": name, "line": line, "end": end})
            inside.append((line, end))
    return {
        "executable": len(lines),
        "run": len(lines) - len(never),
        "never_run_lines": never,
        "never_run_functions": dead_functions,
    }


def main(argv: list[str] | None = None) -> int:
    own, command = split_argv(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="Run the tests and list the code they never execute.",
        usage="%(prog)s --src DIR [--src DIR ...] [options] [-- test command ...]",
    )
    parser.add_argument("--src", action="append", required=True, type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--show-output", action="store_true")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--tracer", choices=("auto", "settrace"), default="auto")
    args = parser.parse_args(own)
    command = command or [sys.executable, "-m", "pytest", "-q"]

    missing = [str(s) for s in args.src if not s.exists()]
    if missing:
        parser.error(f"--src not found: {', '.join(missing)}")
    files = source_files(args.src)
    if not files:
        parser.error("no .py files under --src")

    work = Path(tempfile.mkdtemp(prefix="py_coverage_"))
    try:
        boot_dir, data_dir = work / "boot", work / "data"
        boot_dir.mkdir()
        data_dir.mkdir()
        shutil.copyfile(BOOT, boot_dir / "sitecustomize.py")
        env = dict(os.environ)
        env["PYCOV_OUT"] = str(data_dir)
        env["PYCOV_SRC"] = os.pathsep.join(str(s.resolve()) for s in args.src)
        if args.tracer == "settrace":
            env["PYCOV_TRACER"] = "settrace"
        env["PYTHONPATH"] = os.pathsep.join(
            [str(boot_dir)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        )
        try:
            proc = subprocess.run(
                command, env=env, capture_output=True, text=True, errors="replace", timeout=args.timeout
            )
            exit_code, stdout, stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            exit_code = 124

            def text(value) -> str:
                return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")

            stdout = text(exc.stdout) + f"\n[py_coverage: stopped after {args.timeout:g}s]"
            stderr = text(exc.stderr)
        except FileNotFoundError as exc:
            print(f"cannot run the test command: {exc}", file=sys.stderr)
            return 127

        run: dict[str, set[int]] = {}
        processes = 0
        for part in data_dir.glob("cov-*.json"):
            try:
                data = json.loads(part.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            processes += 1
            for name, lines in data.items():
                run.setdefault(name, set()).update(lines)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    cwd = Path.cwd().resolve()

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(cwd))
        except ValueError:
            return str(p)

    report = {
        "command": command,
        "exit_code": exit_code,
        "processes": processes,
        "tracer": "settrace" if args.tracer == "settrace" or not hasattr(sys, "monitoring") else "sys.monitoring",
        "files": {},
    }
    for path in files:
        hit = run.get(str(path))
        entry = analyse(path, hit or set())
        entry["imported"] = hit is not None
        report["files"][rel(path)] = entry
    if args.json:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")

    # The end of stdout carries the result line; stderr (warnings) only matters on failure.
    shown = stdout.rstrip().splitlines()
    errors = stderr.rstrip().splitlines() if exit_code != 0 or args.show_output else []
    if not args.show_output:
        shown, errors = shown[-8:], errors[-8:]
    print(f"TESTS: {' '.join(command)}  ->  exit {exit_code}")
    for line in shown:
        print(f"  | {line}")
    for line in errors:
        print(f"  ! {line}")
    if exit_code != 0:
        print("  The tests did not pass, so code they would have run may be listed below.")
    if processes == 0:
        print("\nNo Python process reported any lines. Does the command run Python without -I/-S,")
        print("and does --src point at the code the tests import?")
        return exit_code or 1

    entries = report["files"]
    total = sum(e.get("executable", 0) for e in entries.values())
    ran = sum(e.get("run", 0) for e in entries.values())
    pct = 100.0 * ran / total if total else 100.0
    print(
        f"\nCOVERAGE: {ran} of {total} executable lines ran ({pct:.1f}%) in {len(entries)} files, "
        f"{processes} Python processes measured ({report['tracer']})."
    )

    imported_partly = [(n, e) for n, e in entries.items() if e.get("imported") and e.get("never_run_lines")]
    never_imported = [(n, e) for n, e in entries.items() if not e.get("imported") and e.get("executable")]
    complete = [n for n, e in entries.items() if e.get("imported") and not e.get("never_run_lines")]
    broken = [(n, e) for n, e in entries.items() if "error" in e]

    if imported_partly:
        print("\nNEVER RUN BY THE TESTS")
        for name, e in imported_partly:
            print(f"{name}  {e['run']}/{e['executable']} lines")
            if e["never_run_functions"]:
                fns = ", ".join(f"{f['name']} ({f['line']}-{f['end']})" for f in e["never_run_functions"])
                print(f"  functions: {fns}")
            print(f"  lines: {ranges(e['never_run_lines'])}")
    if never_imported:
        print("\nNEVER IMPORTED BY THE TESTS")
        for name, e in never_imported:
            print(f"{name}  ({e['executable']} lines)")
    for name, e in broken:
        print(f"\n{name}: {e['error']}")
    if complete:
        if args.all:
            print("\nFULLY RUN")
            for name in complete:
                print(name)
        else:
            print(f"\nFully run: {len(complete)} files (--all lists them).")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

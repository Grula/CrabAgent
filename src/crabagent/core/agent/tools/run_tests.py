"""run_tests: run a test suite and hand back a compact report.

Raw test output run through ``bash`` stays in the agent's context and is re-sent on
every later step. This tool reads the runner's machine report instead (pytest's
JUnit XML, vitest's JSON) and returns the counts plus, for each failure, the test
id and a short traceback. It remembers the failures, so the next call can re-run
only those.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from crabagent.core.agent.tools.bash import (
    IS_WINDOWS,
    _build_subprocess_kwargs,
    _get_subprocess_env,
    with_user_profile,
)
from crabagent.core.agent.tools.path_utils import get_workspace_root
from crabagent.core.agent.tools.registry import registry
from crabagent.core.agent.tools.sandbox import validate_command

DEFAULT_COMMANDS = {"pytest": "python3 -m pytest", "vitest": "npx vitest run"}
DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 1800
DETAILED_FAILURES = 8  # failures shown with a traceback; the rest are listed by id
LISTED_FAILURES = 50
TRACE_LINES = 25
TRACE_CHARS = 2000
TAIL_LINES = 40  # output shown when the runner wrote no report

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# (workdir, framework) -> test ids that failed in the last run there.
_LAST_FAILURES: dict[tuple[str, str], list[str]] = {}


# ── Running ─────────────────────────────────────────────────────────────────


async def _run(command: str, workdir: str, timeout: float) -> tuple[int | None, str]:
    """(exit code, combined output). Exit code None means it was stopped at the timeout."""
    proc = await asyncio.create_subprocess_shell(
        with_user_profile(command),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_get_subprocess_env(),
        **_build_subprocess_kwargs(workdir),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, out.decode("utf-8", errors="replace")
    except TimeoutError:
        _kill_tree(proc)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception:
            out = b""
        return None, out.decode("utf-8", errors="replace")


def _kill_tree(proc) -> None:
    try:
        if IS_WINDOWS:
            proc.kill()
        else:  # started in its own session, so the group holds the runner and its workers
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _q(arg: str) -> str:
    """Quote one argument for the shell that will run it."""
    return subprocess.list2cmdline([arg]) if IS_WINDOWS else shlex.quote(arg)


def _tail(output: str, lines: int = TAIL_LINES) -> str:
    kept = _ANSI.sub("", output).rstrip().splitlines()[-lines:]
    return "\n".join(kept) if kept else "(no output)"


# Stack frames inside the test runner or Node itself, which never locate the bug.
_NOISE_FRAME = re.compile(r"node_modules[/\\]|node:internal|\(<anonymous>\)")


def _trace(text: str, message: str = "") -> str:
    """The end of a traceback, which is where the failing assertion is."""
    lines = [ln for ln in _ANSI.sub("", text or "").rstrip().splitlines() if not _NOISE_FRAME.search(ln)]
    while lines and message and lines[0].strip() == message.strip():
        lines = lines[1:]  # already shown as the message
    if len(lines) > TRACE_LINES:
        lines = [f"... ({len(lines) - TRACE_LINES} lines above omitted)"] + lines[-TRACE_LINES:]
    out = "\n".join(lines)
    return out if len(out) <= TRACE_CHARS else "..." + out[-TRACE_CHARS:]


# ── pytest ──────────────────────────────────────────────────────────────────


def _pytest_node_id(case: ET.Element) -> str:
    name = case.get("name", "")
    classname = case.get("classname", "")
    file = case.get("file")
    if not classname:  # a collection error: the whole file failed to import
        return file or name
    if file:
        module = file[:-3].replace("/", ".").replace("\\", ".") if file.endswith(".py") else ""
        rest = classname[len(module):].lstrip(".") if module and classname.startswith(module) else ""
        return "::".join(p for p in (file, rest.replace(".", "::"), name) if p)
    return "::".join(p for p in (classname, name) if p)


def parse_pytest_junit(xml_text: str) -> dict:
    """Counts and failures from pytest's JUnit XML (junit_family=xunit1)."""
    root = ET.fromstring(xml_text)
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    failures: list[dict] = []
    for case in root.iter("testcase"):
        bad = case.find("failure")
        kind = "failed"
        if bad is None:
            bad = case.find("error")
            kind = "errors"
        if bad is not None:
            counts[kind] += 1
            message = (bad.get("message") or "").strip()
            failures.append({
                "id": _pytest_node_id(case),
                "file": case.get("file") or "",
                "kind": "error" if kind == "errors" else "failed",
                "message": message.splitlines()[0] if message else "",
                "trace": bad.text or "",
            })
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return {"counts": counts, "failures": failures}


def _pytest_command(base: str, targets: list[str], keyword: str, report: str) -> str:
    parts = [base, "-q", "--tb=short", "-o", "junit_family=xunit1", "--junitxml", _q(report)]
    if keyword:
        parts += ["-k", _q(keyword)]
    parts += [_q(t) for t in targets]
    return " ".join(parts)


_PYTEST_EXIT = {
    2: "the run was interrupted",
    3: "pytest hit an internal error",
    4: "pytest was called wrongly (bad option or path)",
    5: "no tests were collected",
}


# ── vitest ──────────────────────────────────────────────────────────────────


def parse_vitest_json(json_text: str, workdir: str) -> dict:
    """Counts and failures from vitest's (jest-compatible) JSON report."""
    data = json.loads(json_text)
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    failures: list[dict] = []
    for suite in data.get("testResults") or []:
        file = suite.get("name") or ""
        try:
            file = str(Path(file).resolve().relative_to(Path(workdir).resolve()))
        except (ValueError, OSError):
            pass
        cases = suite.get("assertionResults") or []
        for case in cases:
            status = case.get("status")
            if status == "passed":
                counts["passed"] += 1
            elif status == "failed":
                counts["failed"] += 1
                trace = "\n".join(case.get("failureMessages") or [])
                failures.append({
                    "id": f"{file} > {case.get('fullName') or case.get('title') or '?'}",
                    "file": file,
                    "kind": "failed",
                    "message": _ANSI.sub("", trace).strip().splitlines()[0] if trace.strip() else "",
                    "trace": trace,
                })
            else:  # skipped, pending, todo
                counts["skipped"] += 1
        # A file that failed to load (syntax error, bad import) has no test results.
        if suite.get("status") == "failed" and not any(c.get("status") == "failed" for c in cases):
            counts["errors"] += 1
            message = suite.get("message") or ""
            failures.append({
                "id": file,
                "file": file,
                "kind": "error",
                "message": _ANSI.sub("", message).strip().splitlines()[0] if message.strip() else "",
                "trace": message,
            })
    return {"counts": counts, "failures": failures}


def _vitest_command(base: str, targets: list[str], keyword: str, report: str) -> str:
    parts = [base, "--reporter=json", f"--outputFile={_q(report)}"]
    if keyword:
        parts += ["-t", _q(keyword)]
    parts += [_q(t) for t in targets]
    return " ".join(parts)


# ── Report ──────────────────────────────────────────────────────────────────


def _count_line(counts: dict) -> str:
    order = (("failed", "failed"), ("errors", "error"), ("passed", "passed"), ("skipped", "skipped"))
    parts = []
    for key, label in order:
        n = counts.get(key, 0)
        if n:
            parts.append(f"{n} {label}{'s' if key == 'errors' and n != 1 else ''}")
    return ", ".join(parts) or "no tests ran"


def format_report(framework: str, result: dict, exit_code: int | None, seconds: float) -> str:
    counts, failures = result["counts"], result["failures"]
    head = f"{_count_line(counts)} in {seconds:.1f}s ({framework}"
    if not failures:
        if counts.get("passed", 0) == 0:
            return f"ERROR: {head}, exit code {exit_code}): no tests ran. Check the target and -k/-t filter."
        return f"PASSED: {head})"
    lines = [f"FAILED: {head}, exit code {exit_code})", ""]
    if framework == "pytest" and exit_code == 2 and counts.get("errors") and not counts.get("passed"):
        lines[2:2] = [
            "pytest stopped before running any test: a test file failed to import. Fix it, or "
            "leave that file out with `target`.",
            "",
        ]
    for i, f in enumerate(failures[:DETAILED_FAILURES], 1):
        lines.append(f"{i}. {f['id']}" + (" [error]" if f["kind"] == "error" else ""))
        if f["message"]:
            lines.append(f"   {f['message'][:300]}")
        trace = _trace(f["trace"], f["message"])
        if trace:
            lines.append("\n".join("   " + ln for ln in trace.splitlines()))
        lines.append("")
    rest = failures[DETAILED_FAILURES:]
    if rest:
        lines.append(f"Also failing ({len(rest)}, no details):")
        lines += [f"  - {f['id']}" for f in rest[:LISTED_FAILURES]]
        if len(rest) > LISTED_FAILURES:
            lines.append(f"  ... and {len(rest) - LISTED_FAILURES} more")
        lines.append("")
    lines.append("Re-run only these: run_tests(failed_only=true, same command/workdir).")
    return "\n".join(lines).rstrip()


# ── Tool ────────────────────────────────────────────────────────────────────


_DESCRIPTION = (
    "Run tests and get a compact report: pass/fail counts, then for each failing test its id and a "
    "short traceback. Use this instead of bash to run tests: raw test output stays in your context "
    "for every later step. Failures are remembered, so `failed_only=true` re-runs just those. "
    "Supports pytest (default) and vitest. If the project needs a special invocation (env vars, a "
    'venv, a plugin flag), pass it as `command`, e.g. "PYTHONPATH=src python3 -m pytest -p '
    'no:cacheprovider"; this tool adds its own reporting flags to it.'
)


@registry.register(
    name="run_tests",
    description=_DESCRIPTION,
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": (
                    "Space-separated test files, directories or test ids "
                    '(e.g. "tests/test_x.py tests/test_y.py::test_z"). Empty runs the whole suite.'
                ),
            },
            "keyword": {
                "type": "string",
                "description": "Only tests matching this name filter (pytest -k, vitest -t).",
            },
            "framework": {
                "type": "string",
                "enum": ["pytest", "vitest"],
                "description": "Test runner. Default: pytest.",
            },
            "command": {
                "type": "string",
                "description": (
                    "Base command that starts the runner, without reporting flags. Default: "
                    '"python3 -m pytest" or "npx vitest run".'
                ),
            },
            "workdir": {
                "type": "string",
                "description": 'Directory to run in, relative to the workspace (e.g. "frontend"). Default: workspace.',
            },
            "failed_only": {
                "type": "boolean",
                "description": "Re-run only the tests that failed in the previous run_tests call here.",
            },
            "timeout": {
                "type": "integer",
                "description": f"Seconds before the run is stopped. Default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}.",
            },
        },
        "required": [],
    },
    requires_permission=True,
    metadata={"source": "builtin", "category": "execution"},
)
async def run_tests(
    target: str = "",
    keyword: str = "",
    framework: str = "pytest",
    command: str = "",
    workdir: str = "",
    failed_only: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    context: Any = None,
) -> str:
    framework = (framework or "pytest").strip().lower()
    if framework not in DEFAULT_COMMANDS:
        return f"Error: unknown framework {framework!r}; use one of: {', '.join(DEFAULT_COMMANDS)}"

    root = get_workspace_root(context) or Path.cwd()
    cwd = Path(workdir).expanduser() if workdir else root
    if not cwd.is_absolute():
        cwd = root / cwd
    if not cwd.is_dir():
        return f"Error: workdir {str(cwd)!r} is not a directory"
    cwd_str = str(cwd.resolve())
    key = (cwd_str, framework)

    targets = shlex.split(target, posix=not IS_WINDOWS) if target else []
    if failed_only:
        previous = _LAST_FAILURES.get(key)
        if not previous:
            return "Error: no failures recorded from an earlier run_tests call in this directory; run the tests first."
        if framework == "vitest":  # vitest filters by file; re-run the files that had failures
            targets = list(dict.fromkeys(previous))
        else:
            targets = previous
        keyword = ""

    timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    base = (command or DEFAULT_COMMANDS[framework]).strip()
    report_dir = tempfile.mkdtemp(prefix="crabagent-run-tests-")
    try:
        report = os.path.join(report_dir, "report.xml" if framework == "pytest" else "report.json")
        build = _pytest_command if framework == "pytest" else _vitest_command
        full = build(base, targets, keyword, report)

        block = validate_command(full)
        if block:
            if not block.startswith("NEED_CONFIRM:"):
                return block
            detail = block[len("NEED_CONFIRM:"):]
            if not (context and getattr(context, "confirm_callback", None)):
                return f"Error: this command needs your confirmation ({detail}); run it with bash instead."
            if not await context.confirm_callback("run_tests", {"command": full, "warning": detail}):
                return f"Command denied by user: {detail}"

        started = time.monotonic()
        try:
            exit_code, output = await _run(full, cwd_str, timeout)
        except FileNotFoundError as exc:
            return f"Error: could not start the test command: {exc}"
        seconds = time.monotonic() - started

        if exit_code is None:
            return (
                f"ERROR: tests still running after {timeout}s; stopped them. Narrow the run with "
                f"`target`/`keyword`, or raise `timeout`.\nLast lines of output:\n{_tail(output)}"
            )

        text = Path(report).read_text(encoding="utf-8", errors="replace") if os.path.exists(report) else ""
        if not text.strip():
            why = _PYTEST_EXIT.get(exit_code, "") if framework == "pytest" else ""
            return (
                f"ERROR: {framework} exited with code {exit_code}{f' ({why})' if why else ''} "
                f"and wrote no report. Command: {full}\nLast lines of output:\n{_tail(output)}"
            )
        try:
            result = parse_pytest_junit(text) if framework == "pytest" else parse_vitest_json(text, cwd_str)
        except (ET.ParseError, ValueError) as exc:
            return f"ERROR: could not read the {framework} report ({exc}).\nLast lines of output:\n{_tail(output)}"

        failures = result["failures"]
        if framework == "vitest":
            remembered = [f["file"] for f in failures if f["file"]]
        else:
            # A collection error has no test id to re-run; its file re-runs instead.
            remembered = [f["id"] if "::" in f["id"] else f["file"] or f["id"] for f in failures]
        if remembered:
            _LAST_FAILURES[key] = list(dict.fromkeys(remembered))
        elif not targets and not keyword:
            _LAST_FAILURES.pop(key, None)  # a clean full run clears the memory
        elif failed_only:
            _LAST_FAILURES.pop(key, None)  # everything that failed now passes

        report_text = format_report(framework, result, exit_code, seconds)
        if not failures and exit_code not in (0, None) and report_text.startswith("PASSED"):
            # Tests passed but the runner still failed (e.g. a coverage threshold, a warning-as-error).
            report_text = (
                f"FAILED: all tests passed, but {framework} exited with code {exit_code}.\n"
                f"Last lines of output:\n{_tail(output, 15)}"
            )
        return report_text
    finally:
        shutil.rmtree(report_dir, ignore_errors=True)

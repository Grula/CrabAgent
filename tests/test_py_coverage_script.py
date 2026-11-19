"""The dead-code-audit skill's py_coverage.py: what the tests never run, across processes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src/crabagent/skills/dead-code-audit/scripts/py_coverage.py"
)

CORE = '''"""Core."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import decimal


def used(x):
    if x > 0:
        return "positive"
    return "other"


def unused():
    value = 1
    return value


class Thing:
    def method(self):
        return 1

    def never(self):
        return 2


def guarded():  # pragma: no cover
    return 3


if __name__ == "__main__":
    used(1)
'''

CLI = '''import sys


def main():
    print("cli ran")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

SERVER = '''import time


def serve():
    print("ready", flush=True)
    while True:
        time.sleep(0.05)


if __name__ == "__main__":
    serve()
'''

CHECKS = '''import os
import subprocess
import sys

from pkg.core import Thing, used

assert used(5) == "positive"
assert Thing().method() == 1
root = os.path.dirname(os.path.abspath(__file__))
# Tests often start the program with an env of their own that replaces PYTHONPATH.
env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": root}
out = subprocess.run([sys.executable, "-m", "pkg.cli"], env=env, cwd=root, capture_output=True, text=True)
assert "cli ran" in out.stdout, out
server = subprocess.Popen([sys.executable, "-m", "pkg.server"], env=env, cwd=root, stdout=subprocess.PIPE, text=True)
assert server.stdout.readline().strip() == "ready"
server.terminate()
assert server.wait(timeout=10) == -15  # still dies of SIGTERM while measured
print("checks ok")
'''


@pytest.fixture
def project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""pkg."""\n')
    (pkg / "core.py").write_text(CORE)
    (pkg / "cli.py").write_text(CLI)
    (pkg / "server.py").write_text(SERVER)
    (pkg / "never.py").write_text("def nothing():\n    return 0\n")
    (tmp_path / "run_checks.py").write_text(CHECKS)
    (tmp_path / "tmp").mkdir()
    return tmp_path


def run_coverage(project: Path, *extra: str) -> tuple[subprocess.CompletedProcess, dict]:
    out = project / "coverage.json"
    env = {"PATH": "/usr/bin:/bin", "TMPDIR": str(project / "tmp")}
    command = [sys.executable, str(SCRIPT), "--src", "pkg", "--json", str(out), *extra]
    proc = subprocess.run(
        [*command, "--", sys.executable, "run_checks.py"],
        cwd=project, env=env, capture_output=True, text=True, timeout=120,
    )
    return proc, json.loads(out.read_text())


@pytest.mark.parametrize("tracer", ["auto", "settrace"])
def test_lists_what_the_tests_never_run_in_every_process(project: Path, tracer: str):
    proc, report = run_coverage(project, "--tracer", tracer)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "checks ok" in proc.stdout
    assert report["processes"] >= 3  # the checks, the CLI and the SIGTERM'd server

    core = report["files"]["pkg/core.py"]
    dead = {f["name"] for f in core["never_run_functions"]}
    assert dead == {"unused", "Thing.never"}
    assert 11 in core["never_run_lines"]  # return "other": the branch no test takes
    # Excluded: the TYPE_CHECKING import, the main guard, the pragma'd function.
    assert not {5, 28, 29, 32, 33} & set(core["never_run_lines"])

    # Ran only in subprocesses, one of them with a replaced PYTHONPATH, one killed by SIGTERM.
    assert report["files"]["pkg/cli.py"]["never_run_lines"] == []
    assert report["files"]["pkg/server.py"]["never_run_lines"] == []

    assert report["files"]["pkg/never.py"]["imported"] is False
    assert "NEVER IMPORTED BY THE TESTS\npkg/never.py" in proc.stdout
    assert "functions: unused (14-16), Thing.never (23-24)" in proc.stdout
    assert list((project / "tmp").iterdir()) == []  # its scratch folder is gone


def test_passes_on_the_test_commands_failure(project: Path):
    (project / "run_checks.py").write_text("from pkg.core import used\nused(1)\nraise SystemExit(3)\n")
    proc, report = run_coverage(project)
    assert proc.returncode == 3
    assert report["exit_code"] == 3
    assert "The tests did not pass" in proc.stdout

"""run_tests: a compact test report instead of raw runner output in the agent's context."""

from __future__ import annotations

import json
import sys
import textwrap
from types import SimpleNamespace

import pytest

import crabagent.core  # noqa: F401  (registers the built-in tools)
from crabagent.core.agent.tool_telemetry import classify_tool_outcome
from crabagent.core.agent.tools import run_tests as rt
from crabagent.core.agent.tools.registry import registry, resolve_permission

PYTEST = f"{sys.executable} -m pytest -p no:cacheprovider"

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="1" skipped="1" tests="5">
  <testcase classname="tests.test_a" file="tests/test_a.py" line="3" name="test_ok" time="0.01"/>
  <testcase classname="tests.test_a.TestGroup" file="tests/test_a.py" line="9" name="test_p[2]" time="0.01">
    <failure message="assert 2 == 1">tests/test_a.py:10: in test_p
    assert n == 1
E   assert 2 == 1</failure>
  </testcase>
  <testcase classname="tests.test_a" file="tests/test_a.py" line="12" name="test_skip" time="0">
    <skipped type="pytest.skip" message="skipped">skip</skipped>
  </testcase>
  <testcase classname="" file="tests/test_broken.py" name="tests.test_broken" time="0">
    <error message="collection failure">ModuleNotFoundError: No module named 'nope'</error>
  </testcase>
  <testcase classname="tests.test_b" file="tests/test_b.py" line="1" name="test_two" time="0.01"/>
</testsuite></testsuites>"""


def _ctx(path):
    return SimpleNamespace(workspace=str(path), confirm_callback=None)


# ── Reading the runners' reports ──────────────────────────────────────────────


def test_pytest_report_gives_counts_and_rerunnable_ids():
    result = rt.parse_pytest_junit(JUNIT)

    assert result["counts"] == {"passed": 2, "failed": 1, "errors": 1, "skipped": 1}
    ids = [f["id"] for f in result["failures"]]
    assert ids == ["tests/test_a.py::TestGroup::test_p[2]", "tests/test_broken.py"]
    assert result["failures"][0]["message"] == "assert 2 == 1"
    assert result["failures"][1]["kind"] == "error"


def test_vitest_report_gives_counts_and_failing_files(tmp_path):
    report = {
        "testResults": [
            {
                "name": str(tmp_path / "src" / "a.test.ts"),
                "status": "failed",
                "assertionResults": [
                    {"fullName": "math adds", "status": "passed", "failureMessages": []},
                    {"fullName": "math breaks", "status": "failed",
                     "failureMessages": ["AssertionError: expected 2 to be 3\n    at src/a.test.ts:9:5"]},
                    {"fullName": "math later", "status": "todo", "failureMessages": []},
                ],
            },
            {"name": str(tmp_path / "src" / "b.test.ts"), "status": "failed",
             "message": "SyntaxError: Unexpected token", "assertionResults": []},
        ]
    }

    result = rt.parse_vitest_json(json.dumps(report), str(tmp_path))

    assert result["counts"] == {"passed": 1, "failed": 1, "errors": 1, "skipped": 1}
    assert [f["id"] for f in result["failures"]] == ["src/a.test.ts > math breaks", "src/b.test.ts"]
    assert result["failures"][0]["message"] == "AssertionError: expected 2 to be 3"


def test_report_shows_details_for_the_first_failures_and_lists_the_rest():
    failures = [
        {"id": f"tests/t.py::test_{i}", "file": "tests/t.py", "kind": "failed", "message": f"boom {i}",
         "trace": "\n".join(f"line {n}" for n in range(60))}
        for i in range(rt.DETAILED_FAILURES + 3)
    ]
    text = rt.format_report("pytest", {"counts": {"failed": len(failures), "passed": 7}, "failures": failures}, 1, 2.0)

    assert text.startswith(f"FAILED: {len(failures)} failed, 7 passed in 2.0s (pytest, exit code 1)")
    assert "boom 0" in text and f"boom {rt.DETAILED_FAILURES - 1}" in text
    assert f"boom {rt.DETAILED_FAILURES}" not in text  # listed by id only
    assert f"  - tests/t.py::test_{rt.DETAILED_FAILURES}" in text
    assert "line 59" in text and "line 0\n" not in text  # the end of each traceback is kept
    assert "failed_only=true" in text


def test_a_clean_run_is_one_line():
    text = rt.format_report("pytest", {"counts": {"passed": 12, "skipped": 1}, "failures": []}, 0, 1.25)

    assert text == "PASSED: 12 passed, 1 skipped in 1.2s (pytest)"


# ── Running pytest for real ───────────────────────────────────────────────────


def _project(tmp_path, body: str):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_demo.py").write_text(textwrap.dedent(body))
    return tmp_path


@pytest.mark.asyncio
async def test_failures_are_reported_and_can_be_rerun_alone(tmp_path):
    project = _project(tmp_path, """
        def test_ok():
            assert True

        def test_bad():
            assert 1 + 1 == 3
    """)

    first = await rt.run_tests(command=PYTEST, context=_ctx(project))

    assert first.startswith("FAILED: 1 failed, 1 passed")
    assert "tests/test_demo.py::test_bad" in first
    assert "assert 1 + 1 == 3" in first or "assert 2 == 3" in first

    again = await rt.run_tests(command=PYTEST, failed_only=True, context=_ctx(project))

    assert again.startswith("FAILED: 1 failed in")  # test_ok was not re-run


@pytest.mark.asyncio
async def test_rerunning_failures_needs_an_earlier_run(tmp_path):
    result = await rt.run_tests(command=PYTEST, failed_only=True, context=_ctx(tmp_path))

    assert result.startswith("Error: no failures recorded")


@pytest.mark.asyncio
async def test_a_run_that_matches_no_tests_says_so(tmp_path):
    project = _project(tmp_path, "def test_ok():\n    assert True\n")

    result = await rt.run_tests(command=PYTEST, keyword="nothing_matches_this", context=_ctx(project))

    assert result.startswith("ERROR:") and "no tests ran" in result


@pytest.mark.asyncio
async def test_a_hanging_run_is_stopped_at_the_timeout(tmp_path):
    project = _project(tmp_path, "import time\n\ndef test_slow():\n    time.sleep(60)\n")

    result = await rt.run_tests(command=PYTEST, timeout=2, context=_ctx(project))

    assert result.startswith("ERROR: tests still running after 2s")


@pytest.mark.asyncio
async def test_a_runner_that_cannot_start_reports_its_output(tmp_path):
    result = await rt.run_tests(command=f"{PYTEST} --no-such-option", context=_ctx(tmp_path))

    assert result.startswith("ERROR: pytest exited with code 4 (pytest was called wrongly")
    assert "no-such-option" in result


# ── Wiring ────────────────────────────────────────────────────────────────────


def test_it_is_a_registered_builtin():
    assert registry.get("run_tests") is not None


def test_it_follows_the_agents_bash_permission():
    assert resolve_permission({"bash": "deny"}, "run_tests") == "deny"
    assert resolve_permission({"bash": "auto"}, "run_tests") == "auto"
    assert resolve_permission({"bash": "deny", "run_tests": "auto"}, "run_tests") == "auto"
    assert resolve_permission({"read": "auto"}, "run_tests") is None  # falls back to asking


def test_failing_runs_count_as_failed_commands_in_the_stats():
    assert classify_tool_outcome("run_tests", "FAILED: 1 failed, 3 passed in 1.0s (pytest, exit code 1)") == (
        False, "exit_nonzero")
    assert classify_tool_outcome("run_tests", "ERROR: tests still running after 600s") == (False, "exit_nonzero")
    assert classify_tool_outcome("run_tests", "PASSED: 3 passed in 1.0s (pytest)") == (True, "ok")

"""Run a feature request through the CrabAgent team end to end and score the result.

Usage (from the repository root):

    PYTHONPATH=src python3 scripts/agent_eval.py scripts/evals/inventory_3step [--out DIR] [--model M]

A task folder holds:
  project/       the starting code, copied into a fresh git workspace (never this repo)
  request.md     the feature request, sent as the first chat message
  acceptance/    hidden tests, copied in only after the agents finish
  task.json      {"agent": "architect", "timeout_minutes": 60, "project_test_command": [...]}

What happens:
1. a preview server starts on a copy of ~/.crabagent/crabagent.db (scripts/ui_check.py
   does the copy, the known password, 127.0.0.1 and the cleanup);
2. a chat is created on the scratch workspace and the request is sent to the agent;
3. while it runs, every pending question or tool confirmation is answered automatically
   (first option / "decide yourself" / approve) and counted as an intervention: an
   autonomous agent should need none;
4. when the main run ends (or times out), the acceptance tests and the project's own
   tests run in the workspace;
5. report.json + report.md go to --out: verdict, test results, interventions, every
   agent run (steps, tokens, status), step-cap hits, the final reply and the diff.

PASS = every acceptance test and every project test passes, with the run completed.
The server and the database copy are always removed; the workspace is kept in --out.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_check  # noqa: E402  (shares the preview-server code)

POLL_SECONDS = 5
AUTO_ANSWER = (
    "Decide this yourself with your recommended option, note it as an assumption, and continue "
    "without asking again. I'm not available until the work is done."
)


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 30):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw) if raw else None


def _ro(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _git(workspace: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=eval", "-c", "user.email=eval@localhost", "-c", "commit.gpgsign=false", *args],
        cwd=workspace, capture_output=True, text=True, check=False,
    ).stdout


def _prepare_workspace(task: Path, out: Path) -> Path:
    workspace = out / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(task / "project", workspace)
    _git(workspace, "init", "-q", "-b", "main")
    (workspace / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n*.json\n.eval_acceptance/\n")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "starting point")
    return workspace


def _answer_pending(api: Api, con: sqlite3.Connection, session_id: str, answered: set[str], log: list) -> None:
    rows = con.execute(
        "select request_key, request_type, question, options from task_requests "
        "where session_id = ? and status = 'pending'",
        (session_id,),
    ).fetchall()
    for row in rows:
        key = row["request_key"]
        if key in answered:
            continue
        answered.add(key)
        kind = row["request_type"]
        try:
            if kind == "approval":
                api.call("POST", f"/api/sessions/{session_id}/tool-confirm", {"confirm_id": key, "approved": True})
                answer = "approved"
            else:
                options = json.loads(row["options"]) if isinstance(row["options"], str) else row["options"]
                answer = options[0] if options else AUTO_ANSWER
                api.call("POST", f"/api/sessions/{session_id}/user-input", {"input_id": key, "answer": answer})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            answer = f"(could not answer: {exc})"
        log.append({"type": kind, "question": (row["question"] or "")[:500], "answer": answer[:300],
                    "at": round(time.monotonic(), 1)})


def _main_run_finished(con: sqlite3.Connection, session_id: str) -> tuple[bool, str]:
    rows = con.execute(
        "select status from agent_runs where session_id = ? and agent_name = 'main' order by id", (session_id,)
    ).fetchall()
    if not rows:
        return False, ""
    running = con.execute(
        "select count(*) from agent_runs where session_id = ? and status = 'running'", (session_id,)
    ).fetchone()[0]
    last = rows[-1]["status"]
    return (last != "running" and running == 0), last


def _run_tests(workspace: Path, command: list[str], label: str, timeout: int = 600) -> dict:
    try:
        proc = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=timeout)
        output = proc.stdout + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired:
        output, code = f"{label}: timed out after {timeout}s", -1
    summary = next((line for line in reversed(output.splitlines()) if " passed" in line or " failed" in line
                    or " error" in line or "no tests ran" in line), output.strip().splitlines()[-1:] or [""])
    if isinstance(summary, list):
        summary = summary[0] if summary else ""
    failed = [line for line in output.splitlines() if line.startswith(("FAILED", "ERROR"))]
    return {"label": label, "exit_code": code, "passed": code == 0, "summary": summary.strip(),
            "failures": failed[:30], "output_tail": output[-4000:]}


def _collect(con: sqlite3.Connection, session_id: str) -> dict:
    runs = [dict(r) for r in con.execute(
        "select id, parent_run_id, agent_name, model, status, iterations, tokens_used, tool_calls, elapsed, "
        "substr(task_summary, 1, 300) as task, substr(result_summary, 1, 600) as result, error "
        "from agent_runs where session_id = ? order by id", (session_id,))]
    usage = con.execute(
        "select coalesce(sum(prompt_tokens),0), coalesce(sum(completion_tokens),0), coalesce(sum(cached_tokens),0), "
        "count(*) from token_usage where session_id = ?", (session_id,)).fetchone()
    final = con.execute(
        "select content from messages where conversation_id = (select id from conversations where session_id = ?) "
        "and role = 'assistant' and coalesce(content, '') != '' order by id desc limit 1", (session_id,)).fetchone()
    step_cap = [r for r in runs if r["iterations"] and r["agent_name"] not in ("main", "pipeline")
                and r["iterations"] >= 80]
    return {
        "runs": runs,
        "tokens": {"prompt": usage[0], "completion": usage[1], "cached": usage[2], "llm_calls": usage[3]},
        "step_cap_hits": len(step_cap),
        "failed_runs": [r for r in runs if r["status"] in ("failed", "interrupted")],
        "final_reply": (final[0] if final else "")[:6000],
    }


def _report_md(report: dict) -> str:
    lines = [f"# Eval {report['task']}: {report['verdict']}", ""]
    lines.append(f"- status: {report['run_status']} after {report['elapsed_s']} s; interventions: "
                 f"{len(report['interventions'])}; step-cap hits: {report['metrics'].get('step_cap_hits')}")
    tokens = report["metrics"].get("tokens", {})
    lines.append(f"- tokens: prompt {tokens.get('prompt')}, cached {tokens.get('cached')}, completion "
                 f"{tokens.get('completion')}, LLM calls {tokens.get('llm_calls')}")
    for test in report["tests"]:
        lines.append(f"- {test['label']}: {'PASS' if test['passed'] else 'FAIL'} ({test['summary']})")
        lines += [f"  - {f}" for f in test["failures"][:15]]
    lines += ["", "## Runs", "", "| id | agent | status | steps | tokens | task |", "|---|---|---|---|---|---|"]
    for run in report["metrics"].get("runs", []):
        task = (run["task"] or "").replace("\n", " ").replace("|", "/")[:90]
        lines.append(f"| {run['id']} | {run['agent_name']} | {run['status']} | {run['iterations']} | "
                     f"{run['tokens_used']} | {task} |")
    if report["interventions"]:
        lines += ["", "## Interventions (auto-answered)", ""]
        lines += [f"- {i['type']}: {i['question'][:200]!r} -> {i['answer'][:120]!r}" for i in report["interventions"]]
    lines += ["", "## Final reply", "", report["metrics"].get("final_reply", "")[:4000]]
    if report.get("error"):
        lines += ["", "## Harness error", "", "```", report["error"], "```"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("task", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--model", default="", help="chat model override (default: the app's default)")
    parser.add_argument("--timeout-minutes", type=float, default=None)
    args = parser.parse_args(argv)

    task = args.task.resolve()
    spec = json.loads((task / "task.json").read_text())
    request = (task / "request.md").read_text().strip()
    out = (args.out or Path(tempfile.mkdtemp(prefix=f"agent-eval-{task.name}-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    timeout_s = 60 * (args.timeout_minutes or spec.get("timeout_minutes", 60))
    workspace = _prepare_workspace(task, out)

    for signum in (ui_check.signal.SIGTERM, ui_check.signal.SIGHUP):
        ui_check.signal.signal(signum, ui_check._exit_on_signal)

    report: dict = {"task": task.name, "workspace": str(workspace), "interventions": [], "tests": [],
                    "metrics": {}, "run_status": "not started", "elapsed_s": 0}
    tmp = Path(tempfile.mkdtemp(prefix="agent-eval-"))
    proc = None
    started = time.monotonic()
    try:
        username = ui_check._prepare_db_copy(tmp, None)
        port = ui_check._free_port()
        base_url = f"http://127.0.0.1:{port}"
        proc = ui_check._start_server(tmp, port)
        ui_check._wait_until_up(base_url, proc)
        api = Api(base_url, ui_check._login(base_url, username, ui_check.PREVIEW_PASSWORD))
        session = api.call("POST", "/api/sessions", {"title": f"eval {task.name}", "workspace": str(workspace),
                                                    "model": args.model})
        session_id = session["session_id"]
        report["session_id"] = session_id
        body = {"message": request, "agent": spec.get("agent", "architect")}
        if args.model:
            body["model"] = args.model
        api.call("POST", f"/api/sessions/{session_id}/prompt", body)
        print(f"eval {task.name}: session {session_id}, workspace {workspace}", flush=True)

        answered: set[str] = set()
        db_copy = tmp / "db.sqlite"
        status = "running"
        last_print = 0.0
        while True:
            time.sleep(POLL_SECONDS)
            if proc.poll() is not None:
                status = f"server exited ({proc.returncode})"
                break
            con = _ro(db_copy)
            try:
                _answer_pending(api, con, session_id, answered, report["interventions"])
                done, status = _main_run_finished(con, session_id)
                if time.monotonic() - last_print > 60:
                    last_print = time.monotonic()
                    count = con.execute("select count(*) from agent_runs where session_id = ?",
                                        (session_id,)).fetchone()[0]
                    print(f"  {int(time.monotonic() - started)} s: {count} runs, status {status or 'starting'}",
                          flush=True)
            finally:
                con.close()
            if done:
                break
            if time.monotonic() - started > timeout_s:
                status = "timeout"
                try:
                    api.call("POST", f"/api/sessions/{session_id}/abort", {})
                except (urllib.error.URLError, OSError):
                    pass
                time.sleep(10)
                break
        report["run_status"] = status
        report["elapsed_s"] = round(time.monotonic() - started)
        con = _ro(db_copy)
        try:
            report["metrics"] = _collect(con, session_id)
        finally:
            con.close()
    except SystemExit as exc:
        report["error"] = str(exc)
        ui_check._print_log_tail(tmp)
    except Exception:
        report["error"] = traceback.format_exc(limit=5)
        ui_check._print_log_tail(tmp)
    finally:
        if proc is not None:
            ui_check._stop_server(proc)
        log = tmp / "server.log"
        if log.exists():
            shutil.copy(log, out / "server.log")
        shutil.rmtree(tmp, ignore_errors=True)

    # Score: the hidden acceptance tests plus the project's own tests.
    _git(workspace, "add", "-A")
    report["diff_stat"] = _git(workspace, "diff", "--cached", "--stat")
    (out / "changes.diff").write_text(_git(workspace, "diff", "--cached"))
    acceptance = workspace / ".eval_acceptance"
    shutil.copytree(task / spec.get("acceptance_dir", "acceptance"), acceptance, dirs_exist_ok=True)
    report["tests"].append(_run_tests(
        workspace, [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(acceptance)], "acceptance"))
    command = spec.get("project_test_command")
    if command:
        report["tests"].append(_run_tests(workspace, command, "project tests"))
    shutil.rmtree(acceptance, ignore_errors=True)

    passed = report["run_status"] == "completed" and all(t["passed"] for t in report["tests"])
    report["verdict"] = "PASS" if passed else "FAIL"
    if passed and report["interventions"]:
        report["verdict"] = "PASS (with interventions)"
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "report.md").write_text(_report_md(report))
    print(_report_md(report).split("## Runs")[0])
    print(f"full report: {out / 'report.md'}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

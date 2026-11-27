"""Run a feature request through the CrabAgent team end to end and score the result.

Usage (from the repository root):

    PYTHONPATH=src python3 scripts/agent_eval.py scripts/evals/inventory_3step [--out DIR] [--model M]

A task folder holds:
  project/       the starting code, copied into a fresh git workspace (never this repo);
                 or "project": "repo" in task.json: a copy of this repository's working tree
                 (tracked and untracked, not ignored files), for tasks on a large real codebase
  request.md     the feature request, sent as the first chat message
  acceptance/    hidden tests, copied in only after the agents finish
  task.json      {"agent": "architect", "timeout_minutes": 60, "project_test_command": [...]}, or
                 "checks": [{"label", "command", "cwd"?, "env"?, "timeout"?, "copy"?: [from, to]}]
                 or {"label", "reply_regex"} to check the agent's final reply (e.g. its verdict)
                 run in order; "copy" puts hidden tests in place for that check only.
                 "private_copies": {"in/workspace": "in/repo"} gives the workspace its own copy
                 of a large folder (node_modules: ~5 s), so caches the tools write there
                 (vitest, vite) never touch the real one; deleted again after scoring.
                 ("symlinks" still works for folders nothing ever writes to.)
                 "setup_patch": "bugs.patch" is applied (git apply) and "setup_commands"
                 [{"label", "command", "cwd"?}] run before the starting commit, so a
                 bug-fix task starts from a broken state that git history doesn't reveal.

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


# Agent instructions a repository may keep out of git (this one ignores AGENTS.md
# and .crabagent/): copied anyway, since the agents read them in the real workspace.
AGENT_FILES = ("AGENTS.md", "CLAUDE.md", ".crabagent/skills")
# The eval tasks themselves: their hidden acceptance tests must not be in the
# workspace (a run found and ran them before this).
EXCLUDED_FROM_COPY = ("scripts/evals/",)


def _copy_repo(workspace: Path) -> None:
    """This repository's working tree (tracked + untracked, not ignored) into ``workspace``,
    plus :data:`AGENT_FILES`."""
    repo = ui_check.REPO
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo, capture_output=True, check=True,
    ).stdout.decode().split("\0")
    workspace.mkdir(parents=True)
    for rel in filter(None, listed):
        if rel.startswith(EXCLUDED_FROM_COPY):
            continue
        source = repo / rel
        if not source.is_file():
            continue  # deleted in the working tree
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for rel in AGENT_FILES:
        source, target = repo / rel, workspace / rel
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _prepare_workspace(task: Path, out: Path, spec: dict | None = None) -> Path:
    spec = spec or {}
    workspace = out / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    if spec.get("project") == "repo":
        _copy_repo(workspace)
    else:
        shutil.copytree(task / "project", workspace)
    for dest, source_rel in (spec.get("private_copies") or {}).items():
        source = (ui_check.REPO / source_rel).resolve()
        if source.is_dir() and not (workspace / dest).exists():
            (workspace / dest).parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cp", "-a", str(source), str(workspace / dest)], check=True)
    for link, target in (spec.get("symlinks") or {}).items():
        source = (ui_check.REPO / target).resolve()
        if source.exists() and not (workspace / link).exists():
            (workspace / link).parent.mkdir(parents=True, exist_ok=True)
            (workspace / link).symlink_to(source)
    _git(workspace, "init", "-q", "-b", "main")
    if spec.get("setup_patch"):
        applied = subprocess.run(["git", "apply", "--whitespace=nowarn", str(task / spec["setup_patch"])],
                                 cwd=workspace, capture_output=True, text=True)
        if applied.returncode != 0:
            raise SystemExit(f"setup error: {spec['setup_patch']} does not apply: {applied.stderr.strip()}")
    for step in spec.get("setup_commands") or []:
        done = subprocess.run(step["command"], cwd=workspace / step.get("cwd", ""), capture_output=True, text=True)
        if done.returncode != 0:
            detail = (done.stderr or done.stdout)[-800:]
            raise SystemExit(f"setup error: {step.get('label', step['command'])}: {detail}")
    ignore = workspace / ".gitignore"
    extra = "__pycache__/\n.pytest_cache/\n.eval_acceptance/\n"
    if spec.get("project") != "repo":
        extra += "*.json\n"  # data files the fixture's tests write
    ignore.write_text((ignore.read_text() if ignore.exists() else "") + "\n" + extra)
    for link in list(spec.get("symlinks") or {}) + list(spec.get("private_copies") or {}):
        ignore.write_text(ignore.read_text() + f"/{link}\n")
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


class Settle:
    """Done only after the run looked finished on several polls in a row with no new runs.

    A switch_agent handover starts the next agent's turn about 2 s after the first
    run ends; scoring in that gap would grade half the work.
    """

    def __init__(self, polls: int = 3) -> None:
        self.polls = polls
        self.streak = 0
        self.runs = -1

    def observe(self, finished: bool, run_count: int) -> bool:
        if finished and run_count == self.runs:
            self.streak += 1
        else:
            self.streak = 1 if finished else 0
        self.runs = run_count
        return self.streak >= self.polls


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


def _run_tests(workspace: Path, command: list[str], label: str, timeout: int = 600,
               cwd: str = "", env: dict | None = None) -> dict:
    import os

    full_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run(command, cwd=workspace / cwd, capture_output=True, text=True, timeout=timeout,
                              env=full_env)
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


def _score(task: Path, spec: dict, workspace: Path, report: dict) -> None:
    """Run every check; hidden tests are in place only while their own check runs."""
    for check in _checks(spec):
        if "reply_regex" in check:
            # A check on what the agent reported (e.g. a verdict), not on the workspace.
            import re

            reply = (report.get("metrics") or {}).get("final_reply", "")
            found = re.search(check["reply_regex"], reply, re.IGNORECASE | re.DOTALL)
            report["tests"].append({
                "label": check["label"], "exit_code": 0 if found else 1, "passed": bool(found),
                "summary": f"reply {'matches' if found else 'does not match'} {check['reply_regex']!r}",
                "failures": [], "output_tail": "",
            })
            continue
        copied = None
        if check.get("copy"):
            source, dest = check["copy"]
            copied = workspace / dest
            shutil.copytree(task / source, copied, dirs_exist_ok=True)
        try:
            report["tests"].append(_run_tests(
                workspace, check["command"], check["label"], timeout=check.get("timeout", 600),
                cwd=check.get("cwd", ""), env=check.get("env")))
        finally:
            if copied is not None:
                shutil.rmtree(copied, ignore_errors=True)


def _checks(spec: dict) -> list[dict]:
    """The task's checks; the older acceptance_dir/project_test_command form maps onto them."""
    if spec.get("checks"):
        return spec["checks"]
    checks = [{
        "label": "acceptance",
        "copy": [spec.get("acceptance_dir", "acceptance"), ".eval_acceptance"],
        "command": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", ".eval_acceptance"],
    }]
    if spec.get("project_test_command"):
        checks.append({"label": "project tests", "command": spec["project_test_command"]})
    return checks


def _trace(calls) -> str:
    """"read x5, bash x20 (3 failed: timeout x2, error x1)": what a run spent its steps on."""
    from collections import Counter

    counts: Counter = Counter()
    failed: Counter = Counter()
    reasons: dict[str, Counter] = {}
    for name, ok, reason in calls:
        counts[name] += 1
        if not ok:
            failed[name] += 1
            reasons.setdefault(name, Counter())[reason or "error"] += 1
    parts = []
    for name, n in counts.most_common():
        part = f"{name} x{n}"
        if failed[name]:
            why = ", ".join(f"{r} x{c}" for r, c in reasons[name].most_common(3))
            part += f" ({failed[name]} failed: {why})"
        parts.append(part)
    return ", ".join(parts)


def _call_line(name: str, raw_args: str) -> str:
    """One readable line for a tool call: the command for bash, the files for read/edit, else the args."""
    try:
        args = json.loads(raw_args or "{}")
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    if name in ("bash", "run_tests"):
        detail = " ".join(str(args.get(k, "")) for k in ("command", "target", "workdir") if args.get(k))
    elif name == "read" and args.get("files"):
        detail = ", ".join(str(f.get("file_path", "")) for f in args["files"] if isinstance(f, dict))
    elif args.get("file_path"):
        detail = str(args["file_path"])
    else:
        detail = json.dumps(args, ensure_ascii=False)
    detail = " ".join(detail.split())
    return f"{name}: {detail[:240]}"


def _transcript(con: sqlite3.Connection, session_id: str) -> list[str]:
    """Every tool call of the chat's main agent, in order (sub-agents keep their own context)."""
    rows = con.execute(
        "select tool_calls, agent from messages where conversation_id = (select id from conversations "
        "where session_id = ?) and coalesce(tool_calls, '') not in ('', 'null', '[]') order by id", (session_id,)
    ).fetchall()
    lines = []
    for raw, agent in rows:
        try:
            calls = json.loads(raw)
        except ValueError:
            continue
        for call in calls if isinstance(calls, list) else []:
            function = (call or {}).get("function") or {}
            lines.append(f"[{agent or 'main'}] " + _call_line(function.get("name", "?"), function.get("arguments", "")))
    return lines


def _collect(con: sqlite3.Connection, session_id: str) -> dict:
    runs = [dict(r) for r in con.execute(
        "select id, parent_run_id, agent_name, model, status, iterations, tokens_used, tool_calls, elapsed, "
        "started_at, finished_at, "
        "substr(task_summary, 1, 300) as task, substr(result_summary, 1, 600) as result, error "
        "from agent_runs where session_id = ? order by id", (session_id,))]
    usage = con.execute(
        "select coalesce(sum(prompt_tokens),0), coalesce(sum(completion_tokens),0), coalesce(sum(cached_tokens),0), "
        "count(*) from token_usage where session_id = ?", (session_id,)).fetchone()
    final = con.execute(
        "select content from messages where conversation_id = (select id from conversations where session_id = ?) "
        "and role = 'assistant' and coalesce(content, '') != '' order by id desc limit 1", (session_id,)).fetchone()
    for run in runs:
        calls = con.execute(
            "select tool_name, ok, reason_class from tool_invocations where run_id = ? order by id", (run["id"],)
        ).fetchall()
        run["tool_trace"] = _trace(calls)
    step_cap = [r for r in runs if r["iterations"] and r["agent_name"] not in ("main", "pipeline")
                and r["iterations"] >= 80]
    return {
        "runs": runs,
        "tokens": {"prompt": usage[0], "completion": usage[1], "cached": usage[2], "llm_calls": usage[3]},
        "step_cap_hits": len(step_cap),
        "failed_runs": [r for r in runs if r["status"] in ("failed", "interrupted")],
        "final_reply": (final[0] if final else "")[:6000],
        "transcript": _transcript(con, session_id),
    }


def _clock(value) -> str:
    """HH:MM:SS of a run timestamp, stored as epoch seconds or an ISO string."""
    if value in (None, ""):
        return ""
    try:
        import datetime

        return datetime.datetime.fromtimestamp(float(value)).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(value)[11:19]


def _report_md(report: dict) -> str:
    lines = [f"# Eval {report['task']} ({report.get('agent', 'architect')}): {report['verdict']}", ""]
    lines.append(f"- status: {report['run_status']} after {report['elapsed_s']} s; interventions: "
                 f"{len(report['interventions'])}; step-cap hits: {report['metrics'].get('step_cap_hits')}")
    tokens = report["metrics"].get("tokens", {})
    lines.append(f"- tokens: prompt {tokens.get('prompt')}, cached {tokens.get('cached')}, completion "
                 f"{tokens.get('completion')}, LLM calls {tokens.get('llm_calls')}")
    for test in report["tests"]:
        lines.append(f"- {test['label']}: {'PASS' if test['passed'] else 'FAIL'} ({test['summary']})")
        lines += [f"  - {f}" for f in test["failures"][:15]]
    lines += ["", "## Runs", "", "| id | agent | status | steps | tokens | started | finished | task |",
              "|---|---|---|---|---|---|---|---|"]
    for run in report["metrics"].get("runs", []):
        task = (run["task"] or "").replace("\n", " ").replace("|", "/")[:90]
        start, end = (_clock(run.get(k)) for k in ("started_at", "finished_at"))
        lines.append(f"| {run['id']} | {run['agent_name']} | {run['status']} | {run['iterations']} | "
                     f"{run['tokens_used']} | {start} | {end} | {task} |")
    traced = [r for r in report["metrics"].get("runs", []) if r.get("tool_trace")]
    if traced:
        lines += ["", "## Tool use per run", ""]
        lines += [f"- {r['id']} {r['agent_name']}: {r['tool_trace']}" for r in traced]
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
    parser.add_argument("--agent", default="",
                        help="agent that gets the request (default: task.json's, else architect)")
    args = parser.parse_args(argv)

    task = args.task.resolve()
    spec = json.loads((task / "task.json").read_text())
    if args.agent:
        spec["agent"] = args.agent
    request = (task / "request.md").read_text().strip()
    out = (args.out or Path(tempfile.mkdtemp(prefix=f"agent-eval-{task.name}-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    timeout_s = 60 * (args.timeout_minutes or spec.get("timeout_minutes", 60))
    workspace = _prepare_workspace(task, out, spec)

    for signum in (ui_check.signal.SIGTERM, ui_check.signal.SIGHUP):
        ui_check.signal.signal(signum, ui_check._exit_on_signal)

    report: dict = {"task": task.name, "agent": spec.get("agent", "architect"), "workspace": str(workspace),
                    "interventions": [], "tests": [],
                    "metrics": {}, "run_status": "not started", "elapsed_s": 0}
    tmp = Path(tempfile.mkdtemp(prefix="agent-eval-"))
    proc = None
    started = time.monotonic()
    try:
        username = ui_check._prepare_db_copy(tmp, None)
        port = ui_check._free_port()
        base_url = f"http://127.0.0.1:{port}"
        # Started in an empty folder: nothing in the agents' environment points at this
        # repo, and the server's startup seeding (.crabagent/skills, tools) can't land in
        # the workspace being scored.
        server_cwd = tmp / "server-cwd"
        server_cwd.mkdir()
        proc = ui_check._start_server(tmp, port, cwd=server_cwd)
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
        settle = Settle()
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
                finished, status = _main_run_finished(con, session_id)
                count = con.execute("select count(*) from agent_runs where session_id = ?",
                                    (session_id,)).fetchone()[0]
                if time.monotonic() - last_print > 60:
                    last_print = time.monotonic()
                    print(f"  {int(time.monotonic() - started)} s: {count} runs, status {status or 'starting'}",
                          flush=True)
            finally:
                con.close()
            done = settle.observe(finished, count)
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
    _score(task, spec, workspace, report)
    for dest in spec.get("private_copies") or {}:
        shutil.rmtree(workspace / dest, ignore_errors=True)  # 378 MB per run otherwise

    passed = report["run_status"] == "completed" and all(t["passed"] for t in report["tests"])
    report["verdict"] = "PASS" if passed else "FAIL"
    if passed and report["interventions"]:
        report["verdict"] = "PASS (with interventions)"
    (out / "transcript.txt").write_text("\n".join(report["metrics"].get("transcript", [])) + "\n")
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "report.md").write_text(_report_md(report))
    print(_report_md(report).split("## Runs")[0])
    print(f"full report: {out / 'report.md'}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

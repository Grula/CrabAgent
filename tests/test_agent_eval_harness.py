"""scripts/agent_eval.py: run detection, auto-answers and scoring, without a server."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("agent_eval", _SCRIPTS / "agent_eval.py")
agent_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(agent_eval)


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "db.sqlite"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        create table agent_runs (id integer primary key, session_id text, parent_run_id int, agent_name text,
            model text, status text, iterations int, tokens_used int, tool_calls int, elapsed real,
            started_at text, finished_at text,
            task_summary text, result_summary text, error text);
        create table task_requests (request_key text, session_id text, request_type text, question text,
            options text, status text);
        create table token_usage (session_id text, prompt_tokens int, completion_tokens int, cached_tokens int);
        create table tool_invocations (id integer primary key, run_id int, tool_name text, ok int, reason_class text);
        create table conversations (id integer primary key, session_id text);
        create table messages (id integer primary key, conversation_id int, role text, content text,
            tool_calls text, agent text);
        """
    )
    raw.commit()
    reader = agent_eval._ro(db)  # what the harness uses: read-only
    yield Db(reader, raw)
    raw.close()
    reader.close()


class Db:
    """The harness's read-only connection plus a writer for setting up rows."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    def execute(self, *args):
        return self.reader.execute(*args)


def _exec(con, sql, *args):
    con.writer.execute(sql, args)
    con.writer.commit()


def test_main_run_finished_waits_for_every_run(con):
    assert agent_eval._main_run_finished(con, "s") == (False, "")
    _exec(con, "insert into agent_runs (session_id, agent_name, status) values ('s', 'main', 'running')")
    assert agent_eval._main_run_finished(con, "s") == (False, "running")
    _exec(con, "update agent_runs set status = 'completed'")
    _exec(con, "insert into agent_runs (session_id, agent_name, status) values ('s', 'coder', 'running')")
    assert agent_eval._main_run_finished(con, "s") == (False, "completed")  # a sub-agent still works
    _exec(con, "update agent_runs set status = 'completed' where agent_name = 'coder'")
    assert agent_eval._main_run_finished(con, "s") == (True, "completed")


class _FakeApi:
    def __init__(self):
        self.calls = []

    def call(self, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))


def test_pending_requests_are_answered_once_and_logged(con):
    _exec(con, "insert into task_requests values ('c1', 's', 'approval', 'Allow?', null, 'pending')")
    _exec(con, "insert into task_requests values ('q1', 's', 'choice', 'Which?', ?, 'pending')",
          json.dumps(["A (recommended)", "B"]))
    _exec(con, "insert into task_requests values ('q2', 's', 'input', 'Name?', null, 'pending')")
    _exec(con, "insert into task_requests values ('x', 'other', 'input', 'Not ours', null, 'pending')")
    api, answered, log = _FakeApi(), set(), []

    agent_eval._answer_pending(api, con, "s", answered, log)
    agent_eval._answer_pending(api, con, "s", answered, log)  # still pending in the DB: not re-answered

    assert api.calls == [
        ("POST", "/api/sessions/s/tool-confirm", {"confirm_id": "c1", "approved": True}),
        ("POST", "/api/sessions/s/user-input", {"input_id": "q1", "answer": "A (recommended)"}),
        ("POST", "/api/sessions/s/user-input", {"input_id": "q2", "answer": agent_eval.AUTO_ANSWER}),
    ]
    assert [entry["type"] for entry in log] == ["approval", "choice", "input"]


def test_collect_counts_step_cap_hits_and_failures(con):
    _exec(con, "insert into conversations values (1, 's')")
    _exec(con, "insert into messages (conversation_id, role, content) values (1, 'assistant', 'first')")
    _exec(con, "insert into messages (conversation_id, role, content) values (1, 'assistant', 'Done: all 3.')")
    for name, status, steps in (("main", "completed", 30), ("coder", "completed", 80), ("tester", "failed", 12)):
        _exec(con, "insert into agent_runs (session_id, agent_name, status, iterations, tokens_used) "
              "values ('s', ?, ?, ?, 10)", name, status, steps)
    _exec(con, "insert into token_usage values ('s', 100, 20, 90)")

    metrics = agent_eval._collect(con, "s")

    assert metrics["step_cap_hits"] == 1
    assert [r["agent_name"] for r in metrics["failed_runs"]] == ["tester"]
    assert metrics["tokens"] == {"prompt": 100, "completion": 20, "cached": 90, "llm_calls": 1}
    assert metrics["final_reply"] == "Done: all 3."


def test_run_tests_reports_summary_and_failures(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_ok():\n    pass\n\ndef test_bad():\n    assert 0\n")
    result = agent_eval._run_tests(tmp_path, [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                              "-rf", "test_x.py"], "unit")
    assert not result["passed"]
    assert "1 failed, 1 passed" in result["summary"]
    assert any("test_bad" in line for line in result["failures"])


def test_workspace_is_a_fresh_git_repo_outside_the_task(tmp_path):
    task = tmp_path / "task"
    (task / "project").mkdir(parents=True)
    (task / "project" / "a.py").write_text("x = 1\n")
    workspace = agent_eval._prepare_workspace(task, tmp_path / "out")
    assert (workspace / ".git").is_dir()
    assert agent_eval._git(workspace, "status", "--short") == ""
    assert not (task / "project" / ".git").exists()


def test_old_task_format_maps_onto_checks():
    checks = agent_eval._checks({"project_test_command": ["python3", "-m", "pytest", "tests"]})
    assert [c["label"] for c in checks] == ["acceptance", "project tests"]
    assert checks[0]["copy"] == ["acceptance", ".eval_acceptance"]
    explicit = [{"label": "x", "command": ["true"]}]
    assert agent_eval._checks({"checks": explicit}) is explicit


def test_repo_task_copies_the_working_tree_and_links_node_modules(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / "notes.txt").write_text("untracked but not ignored\n")
    (repo / "secret.env").write_text("TOKEN=1\n")
    (repo / ".crabagent" / "skills" / "run-app").mkdir(parents=True)
    (repo / ".crabagent" / "skills" / "run-app" / "SKILL.md").write_text("how to run\n")
    (repo / "AGENTS.md").write_text("rules\n")
    (repo / "scripts" / "evals" / "t" / "acceptance").mkdir(parents=True)
    (repo / "scripts" / "evals" / "t" / "acceptance" / "test_hidden.py").write_text("x\n")
    (repo / "scripts" / "keep.py").write_text("y\n")
    (repo / ".gitignore").write_text("node_modules/\nsecret.env\nAGENTS.md\n.crabagent/\n")
    agent_eval._git(repo, "init", "-q")
    agent_eval._git(repo, "add", "src", ".gitignore")
    agent_eval._git(repo, "commit", "-qm", "init")
    monkeypatch.setattr(agent_eval.ui_check, "REPO", repo)

    (repo / "frontend" / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n")
    spec = {"project": "repo", "private_copies": {"frontend/node_modules": "frontend/node_modules"}}
    workspace = agent_eval._prepare_workspace(tmp_path / "task", tmp_path / "out", spec)

    assert (workspace / "src" / "a.py").read_text() == "x = 1\n"
    assert (workspace / "notes.txt").exists()          # untracked files come along
    assert not (workspace / "secret.env").exists()     # ignored files don't
    assert (workspace / "AGENTS.md").read_text() == "rules\n"  # ...except agent instructions
    assert (workspace / ".crabagent" / "skills" / "run-app" / "SKILL.md").exists()
    assert not (workspace / "scripts" / "evals").exists()  # the hidden tests never leak
    assert (workspace / "scripts" / "keep.py").exists()
    copied = workspace / "frontend" / "node_modules"
    assert copied.is_dir() and not copied.is_symlink()  # a private copy, not a link
    (copied / ".vite").mkdir()                            # a cache written in the workspace...
    assert not (repo / "frontend" / "node_modules" / ".vite").exists()  # ...never reaches the repo
    assert agent_eval._git(workspace, "status", "--short") == ""  # and git ignores the copy


def test_hidden_tests_are_removed_after_their_check(tmp_path):
    task = tmp_path / "task"
    (task / "hidden").mkdir(parents=True)
    (task / "hidden" / "test_h.py").write_text("def test_h():\n    pass\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    spec = {"checks": [{"label": "hidden", "copy": ["hidden", "tests/eval"],
                        "command": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/eval"]}]}
    report = {"tests": []}
    agent_eval._score(task, spec, workspace, report)
    assert report["tests"][0]["passed"]
    assert not (workspace / "tests" / "eval").exists()


def test_clock_formats_epoch_and_iso():
    import datetime

    epoch = datetime.datetime(2026, 11, 26, 14, 5, 9).timestamp()
    assert agent_eval._clock(epoch) == "14:05:09"
    assert agent_eval._clock("2026-11-26 14:05:09.123") == "14:05:09"
    assert agent_eval._clock(None) == ""


def test_setup_patch_is_applied_before_the_starting_commit(tmp_path):
    task = tmp_path / "task"
    (task / "project").mkdir(parents=True)
    (task / "project" / "a.py").write_text("x = 1\n")
    (task / "bugs.patch").write_text(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    )
    spec = {"setup_patch": "bugs.patch", "setup_commands": [{"label": "mark", "command": ["touch", "built.txt"]}]}
    workspace = agent_eval._prepare_workspace(task, tmp_path / "out", spec)
    assert (workspace / "a.py").read_text() == "x = 2\n"
    assert (workspace / "built.txt").exists()
    assert agent_eval._git(workspace, "status", "--short") == ""          # part of the starting point
    assert agent_eval._git(workspace, "log", "--oneline").count("\n") == 1  # no history of the bug


def test_a_patch_that_does_not_apply_stops_the_eval(tmp_path):
    task = tmp_path / "task"
    (task / "project").mkdir(parents=True)
    (task / "project" / "a.py").write_text("y = 1\n")
    (task / "bugs.patch").write_text(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    )
    with pytest.raises(SystemExit, match="does not apply"):
        agent_eval._prepare_workspace(task, tmp_path / "out", {"setup_patch": "bugs.patch"})


def test_tool_trace_summarises_calls_and_failures():
    calls = [("bash", 1, "ok"), ("bash", 0, "timeout"), ("read", 1, "ok"), ("bash", 0, "timeout"),
             ("bash", 0, "nonzero_exit")]
    assert agent_eval._trace(calls) == "bash x4 (3 failed: timeout x2, nonzero_exit x1), read x1"
    assert agent_eval._trace([]) == ""


def test_transcript_lists_main_agent_calls_readably(con):
    _exec(con, "insert into conversations values (1, 's')")
    calls = [
        {"function": {"name": "bash", "arguments": json.dumps({"command": "cd frontend &&\n npx tsc -b"})}},
        {"function": {"name": "read",
                      "arguments": json.dumps({"files": [{"file_path": "a.py"}, {"file_path": "b.py"}]})}},
        {"function": {"name": "edit",
                      "arguments": json.dumps({"file_path": "a.py", "old_string": "x", "new_string": "y"})}},
        {"function": {"name": "run_tests", "arguments": json.dumps({"command": "pytest", "target": "tests/t.py"})}},
    ]
    _exec(con, "insert into messages (conversation_id, role, content, tool_calls, agent) "
          "values (1, 'assistant', '', ?, 'engineer')", json.dumps(calls))
    assert agent_eval._transcript(con, "s") == [
        "[engineer] bash: cd frontend && npx tsc -b",
        "[engineer] read: a.py, b.py",
        "[engineer] edit: a.py",
        "[engineer] run_tests: pytest tests/t.py",
    ]


def test_settle_waits_out_a_handover_gap():
    settle = agent_eval.Settle(polls=3)
    assert not settle.observe(True, 2)    # the first agent finished...
    assert not settle.observe(False, 3)   # ...and the handover started a new run
    assert not settle.observe(True, 5)
    assert not settle.observe(True, 5)
    assert settle.observe(True, 5)        # finished on 3 polls with no new runs
    fresh = agent_eval.Settle(polls=2)
    assert not fresh.observe(True, 1) and not fresh.observe(True, 2) and fresh.observe(True, 2)


def test_reply_regex_checks_the_final_reply(tmp_path):
    spec = {"checks": [{"label": "item 1 fails", "reply_regex": r"ITEM 1:\s*FAIL"},
                       {"label": "item 2 fails", "reply_regex": r"ITEM 2:\s*FAIL"}]}
    report = {"tests": [], "metrics": {"final_reply": "Item 1: fail (shown with …)\nITEM 2: PASS"}}
    agent_eval._score(tmp_path, spec, tmp_path, report)
    assert [t["passed"] for t in report["tests"]] == [True, False]

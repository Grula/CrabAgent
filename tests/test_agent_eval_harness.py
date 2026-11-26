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
            task_summary text, result_summary text, error text);
        create table task_requests (request_key text, session_id text, request_type text, question text,
            options text, status text);
        create table token_usage (session_id text, prompt_tokens int, completion_tokens int, cached_tokens int);
        create table conversations (id integer primary key, session_id text);
        create table messages (id integer primary key, conversation_id int, role text, content text);
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

"""scripts/ui_check.py: guards that don't need a browser or a server."""

import importlib.util
import signal
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("ui_check", Path(__file__).parent.parent / "scripts" / "ui_check.py")
ui_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ui_check)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:5210", "http://127.0.0.1:5210/#", "http://localhost:5210/x", "http://127.0.0.1"]
)
def test_url_mode_refuses_the_live_server(url, tmp_path, capsys):
    steps = tmp_path / "steps.py"
    steps.write_text("def run(ui):\n    pass\n", encoding="utf-8")
    assert ui_check.main([str(steps), "--url", url]) == 2
    assert "never 5210" in capsys.readouterr().err


def test_sigterm_becomes_systemexit_so_cleanup_runs(tmp_path, monkeypatch):
    # Review finding: SIGTERM (bash_stop) skipped the finally that stops the
    # preview server and deletes the DB copy.
    stopped: list = []
    monkeypatch.setattr(ui_check, "_prepare_db_copy", lambda tmp, user: "u")
    monkeypatch.setattr(ui_check, "_start_server", lambda tmp, port: object())
    monkeypatch.setattr(ui_check, "_stop_server", lambda proc: stopped.append(proc))

    def killed(*_args):
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(ui_check, "_wait_until_up", killed)
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    steps = tmp_path / "steps.py"
    steps.write_text("def run(ui):\n    pass\n", encoding="utf-8")
    try:
        assert ui_check.main([str(steps), "--out", str(tmp_path / "shots")]) == 2
    finally:
        for signum, handler in old.items():
            signal.signal(signum, handler)
    assert len(stopped) == 1


def test_the_database_to_copy_follows_crab_db_url(tmp_path, monkeypatch):
    # Inside an eval, HOME is fake: ~/.crabagent has no database, CRAB_DB_URL names the real one.
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    monkeypatch.setenv("CRAB_DB_URL", f"sqlite+aiosqlite:///{db}")
    assert ui_check._source_db() == db
    monkeypatch.setenv("CRAB_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'missing.sqlite'}")
    assert ui_check._source_db() == Path.home() / ".crabagent" / "crabagent.db"
    monkeypatch.delenv("CRAB_DB_URL")
    assert ui_check._source_db() == Path.home() / ".crabagent" / "crabagent.db"


def test_preview_server_env_does_not_point_agents_at_this_repo(tmp_path, monkeypatch):
    # The eval's agents inherit the server's environment: a PYTHONPATH to this
    # repo's src made their `python3 -c "import crabagent"` load this repo's code.
    seen = {}
    real_popen = ui_check.subprocess.Popen

    def fake_popen(cmd, *args, **kwargs):
        if "--serve" not in cmd:
            return real_popen(cmd, *args, **kwargs)  # the site --user-base lookup
        seen.update(cmd=cmd, cwd=kwargs.get("cwd"), env=kwargs.get("env"))
        return None

    monkeypatch.setattr(ui_check.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    (tmp_path / "ws").mkdir()
    ui_check._start_server(tmp_path, 4321, cwd=tmp_path / "ws")

    assert "PYTHONPATH" not in seen["env"]
    if (Path.home() / ".gitconfig").exists():
        assert seen["env"]["GIT_CONFIG_GLOBAL"] == str(Path.home() / ".gitconfig")  # agents' commits work
    assert seen["cwd"] == str(tmp_path / "ws")
    assert seen["cmd"][1] == "-c" and seen["cmd"][3] == str(ui_check.REPO / "src")  # on sys.path only
    assert seen["cmd"][-3:] == ["--serve", "--port", "4321"]


class _FakePage:
    def on(self, *_args):
        pass


def _ui(workspace):
    return ui_check.UI(_FakePage(), "http://127.0.0.1:1", "t", workspace / "shots", workspace)


def test_seed_file_creates_and_cleanup_removes_only_what_it_made(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "keep.md").write_text("mine\n")
    ui = _ui(tmp_path)
    ui.seed_file("docs/x-plan.md", "# X\n")
    ui.seed_file("new/deep/y.md", "# Y\n")
    assert (tmp_path / "docs" / "x-plan.md").read_text() == "# X\n"

    ui_check._cleanup_seeded(ui)

    assert not (tmp_path / "docs" / "x-plan.md").exists()
    assert not (tmp_path / "new").exists()             # folders it created go too...
    assert (tmp_path / "docs" / "keep.md").exists()    # ...nothing else does


def test_seed_file_refuses_to_overwrite_or_escape(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rules\n")
    ui = _ui(tmp_path)
    with pytest.raises(FileExistsError):
        ui.seed_file("AGENTS.md", "clobbered")
    with pytest.raises(ValueError):
        ui.seed_file("../outside.md", "x")
    assert (tmp_path / "AGENTS.md").read_text() == "rules\n"
    assert not (tmp_path.parent / "outside.md").exists()


def test_seed_file_needs_the_harness_server(tmp_path):
    ui = ui_check.UI(_FakePage(), "http://127.0.0.1:1", "t", tmp_path, None)
    with pytest.raises(RuntimeError, match="not --url"):
        ui.seed_file("docs/x.md", "x")


def test_a_steps_file_or_explore_is_required(capsys):
    assert ui_check.main([]) == 2
    assert "pass a steps file, --do CODE or --explore ROUTE" in capsys.readouterr().err


def test_inline_steps_run_against_the_ui_object():
    seen = []

    class FakeUI:
        def open(self, route):
            seen.append(("open", route))

        def inventory(self):
            seen.append(("inventory",))

    ui_check._inline_steps('ui.open("#/agents"); ui.inventory()')(FakeUI())
    assert seen == [("open", "#/agents"), ("inventory",)]
    with pytest.raises(SyntaxError):
        ui_check._inline_steps("ui.open(")

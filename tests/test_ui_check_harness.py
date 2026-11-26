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

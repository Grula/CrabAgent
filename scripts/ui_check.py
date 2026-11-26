"""Check CrabAgent's UI in a real browser, on a throwaway copy of the database.

Usage (from the repository root):

    PYTHONPATH=src python3 scripts/ui_check.py STEPS.py [--out DIR] [--port N] [--url URL]

STEPS.py is a short file you write for one check, 5-15 lines:

    def run(ui):
        ui.open("#/chat")                      # logged in already
        ui.click("text=New chat")
        ui.fill("textarea", "hello")
        ui.expect_visible("text=hello")        # at most 8 expect_* per run
        ui.screenshot("typed")

What this script does for you, so a check never needs its own 200-line script:
1. copies ~/.crabagent/crabagent.db (sqlite backup), sets a known password on the
   copy's main user, and disables scheduled tasks, MCP servers and email in the copy;
2. starts a preview server on 127.0.0.1 (free port, fake HOME, the real encryption
   key in the environment so saved API keys still decrypt);
3. opens Chromium, logs in through POST /api/auth/login, stores the token as
   crab_token and reloads;
4. runs your steps, then prints one PASS/FAIL line per expectation and the
   screenshot paths;
5. always stops the server and deletes the copy (finally), even on errors.

Exit code: 0 when every expectation passed, 1 on any failure, 2 on a setup error.
--url skips steps 1-2 and 5 and checks an already running server instead (then
pass --user/--password for it).

The user's live database and running server (port 5210) are never touched.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_EXPECTATIONS = 8
REPO = Path(__file__).resolve().parent.parent
LIVE_DB = Path.home() / ".crabagent" / "crabagent.db"
KEY_FILE = Path.home() / ".crabagent" / "encryption_key"
PREVIEW_PASSWORD = "ui-check-preview"


class ExpectationBudget(Exception):
    pass


class UI:
    """The small API a STEPS.py file uses. ``ui.page`` is the Playwright page for anything else."""

    def __init__(self, page, base_url: str, token: str, out_dir: Path) -> None:
        self.page = page
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.out_dir = out_dir
        self.results: list[tuple[bool, str]] = []
        self.screenshots: list[Path] = []
        self.dialogs: list[str] = []
        self.dialog_policy = "accept"
        page.on("dialog", self._on_dialog)

    # ── actions (a failure here stops the run) ──
    def _on_dialog(self, dialog) -> None:
        self.dialogs.append(dialog.message)
        (dialog.accept if self.dialog_policy == "accept" else dialog.dismiss)()

    def open(self, route: str = "") -> None:
        """Open a page of the app: "" (home), "#/chat", "/some/path"."""
        url = self.base_url + "/" + route.lstrip("/") if not route.startswith("#") else f"{self.base_url}/{route}"
        self.page.goto(url, wait_until="load")
        if route.startswith("#"):
            self.page.reload(wait_until="load")  # a hash change alone doesn't restart the app
        self.page.wait_for_timeout(500)

    def click(self, selector: str) -> None:
        self.page.locator(selector).first.click()

    def fill(self, selector: str, text: str) -> None:
        self.page.locator(selector).first.fill(text)

    def type(self, selector: str, text: str, delay_ms: int = 20) -> None:
        self.page.locator(selector).first.press_sequentially(text, delay=delay_ms)

    def press(self, key: str, selector: str | None = None) -> None:
        (self.page.locator(selector).first if selector else self.page.keyboard).press(key)

    def wait_for(self, selector: str, state: str = "visible", timeout_ms: int = 10_000) -> None:
        self.page.locator(selector).first.wait_for(state=state, timeout=timeout_ms)

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    def eval(self, js: str):
        return self.page.evaluate(js)

    def api(self, method: str, path: str, body: dict | None = None):
        """Call the preview server's API as the logged-in user (e.g. to seed a session)."""
        response = self.page.request.fetch(
            self.base_url + path,
            method=method.upper(),
            headers={"Authorization": f"Bearer {self.token}"},
            data=json.dumps(body) if body is not None else None,
        )
        try:
            return response.json()
        except Exception:
            return response.text()

    def screenshot(self, name: str) -> Path:
        path = self.out_dir / f"{len(self.screenshots) + 1:02d}-{name}.png"
        self.page.screenshot(path=str(path), full_page=False)
        self.screenshots.append(path)
        return path

    # ── expectations (recorded; a failed one doesn't stop the run) ──
    def _expect(self, ok: bool, label: str, detail: str = "") -> bool:
        if len(self.results) >= MAX_EXPECTATIONS:
            raise ExpectationBudget(f"more than {MAX_EXPECTATIONS} expectations; split the check")
        self.results.append((ok, label if ok else f"{label}: {detail}"))
        if not ok:
            self.screenshot(f"fail-{len(self.results)}")
        return ok

    def _visible(self, selector: str, timeout_ms: int) -> bool:
        try:
            self.page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False

    def expect_visible(self, selector: str, label: str = "", timeout_ms: int = 5_000) -> bool:
        return self._expect(self._visible(selector, timeout_ms), label or f"visible {selector}", "not visible")

    def expect_hidden(self, selector: str, label: str = "", timeout_ms: int = 5_000) -> bool:
        try:
            self.page.locator(selector).first.wait_for(state="hidden", timeout=timeout_ms)
            ok = True
        except Exception:
            ok = False
        return self._expect(ok, label or f"hidden {selector}", "still visible")

    def expect_text(self, text: str, selector: str = "body", label: str = "", timeout_ms: int = 5_000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        content = ""
        while time.monotonic() < deadline:
            content = self.page.locator(selector).first.inner_text()
            if text in content:
                return self._expect(True, label or f"text {text!r}")
            self.page.wait_for_timeout(200)
        return self._expect(False, label or f"text {text!r}", f"not in {selector} ({content[:120]!r}...)")

    def expect_count(self, selector: str, count: int, label: str = "") -> bool:
        actual = self.page.locator(selector).count()
        return self._expect(actual == count, label or f"{count} x {selector}", f"found {actual}")

    def expect_url_contains(self, part: str, label: str = "") -> bool:
        return self._expect(part in self.page.url, label or f"url contains {part!r}", f"url is {self.page.url}")

    def expect_dialog(self, contains: str = "", label: str = "") -> bool:
        """A browser dialog (alert/confirm) was shown since the last expect_dialog."""
        shown, self.dialogs = self.dialogs, []
        ok = any(contains in message for message in shown) if shown else False
        return self._expect(ok, label or f"dialog {contains!r}", f"dialogs seen: {shown}")

    def expect_eval(self, js: str, expected, label: str = "") -> bool:
        actual = self.page.evaluate(js)
        return self._expect(actual == expected, label or f"{js} == {expected!r}", f"got {actual!r}")


# ── preview server on a copy of the database ─────────────────────────────


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _prepare_db_copy(tmp: Path, username: str | None) -> str:
    """Copy the live DB, set a known password, disable side effects. Returns the username."""
    from crabagent.core.auth_utils import hash_password

    if not LIVE_DB.exists():
        raise SystemExit(f"setup error: {LIVE_DB} not found")
    copy = tmp / "db.sqlite"
    source = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    target = sqlite3.connect(copy)
    try:
        source.backup(target)
    finally:
        source.close()
    try:
        if not username:
            row = target.execute(
                "select u.username from users u left join conversations c on c.user_id = u.id "
                "where u.enabled = 1 group by u.id order by count(c.id) desc limit 1"
            ).fetchone()
            if not row:
                raise SystemExit("setup error: no enabled user in the database")
            username = row[0]
        target.execute(
            "update users set password_hash = ? where username = ?", (hash_password(PREVIEW_PASSWORD), username)
        )
        for table in ("scheduled_tasks", "mcp_servers", "email_configs"):
            try:
                target.execute(f"update {table} set enabled = 0")
            except sqlite3.OperationalError:
                pass
        target.commit()
    finally:
        target.close()
    assert username is not None
    return username


def _start_server(tmp: Path, port: int) -> subprocess.Popen:
    user_site = subprocess.run(
        [sys.executable, "-m", "site", "--user-site"], capture_output=True, text=True, check=False
    ).stdout.strip()
    user_base = subprocess.run(
        [sys.executable, "-m", "site", "--user-base"], capture_output=True, text=True, check=False
    ).stdout.strip()
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(tmp / "home"),
            "CRAB_SERVE_HOST": "127.0.0.1",
            "CRAB_DB_URL": f"sqlite+aiosqlite:///{tmp / 'db.sqlite'}",
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get(
                "PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache" / "ms-playwright")
            ),
            "PYTHONPATH": os.pathsep.join(p for p in (str(REPO / "src"), user_site) if p),
        }
    )
    if user_base:
        # The fake HOME would also hide the user's site-packages from every
        # command the agents run (e.g. `PYTHONPATH=src pytest` then can't import
        # litellm's yaml). PYTHONUSERBASE keeps them visible with any PYTHONPATH.
        env["PYTHONUSERBASE"] = user_base
    if KEY_FILE.exists():
        # Without the real key the copy can't decrypt saved provider keys (401s).
        env["CRAB_ENCRYPTION_KEY"] = KEY_FILE.read_text().strip()
    (tmp / "home" / ".crabagent").mkdir(parents=True, exist_ok=True)  # the server logs there
    log = open(tmp / "server.log", "wb")  # noqa: SIM115 - closed with the process
    return subprocess.Popen(
        [sys.executable, "-m", "crabagent", "--serve", "--port", str(port)],
        cwd=str(REPO),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _wait_until_up(url: str, proc: subprocess.Popen | None, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise SystemExit(f"setup error: preview server exited with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url + "/health", timeout=2):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise SystemExit(f"setup error: preview server not up after {timeout:.0f}s")


def _stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def _login(base_url: str, username: str, password: str) -> str:
    request = urllib.request.Request(
        base_url + "/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())["access_token"]


def _load_steps(path: Path):
    spec = importlib.util.spec_from_file_location("ui_check_steps", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"setup error: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = getattr(module, "run", None)
    if not callable(run):
        raise SystemExit(f"setup error: {path} must define run(ui)")
    return run


def _run_browser(run_steps, base_url: str, token: str, out_dir: Path) -> tuple[UI | None, str]:
    from playwright.sync_api import sync_playwright

    error = ""
    ui = None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(base_url + "/", wait_until="load")
            page.evaluate("token => localStorage.setItem('crab_token', token)", token)
            page.reload(wait_until="load")
            ui = UI(page, base_url, token, out_dir)
            try:
                run_steps(ui)
            except ExpectationBudget as exc:
                error = str(exc)
            except Exception as exc:
                error = f"step failed: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                try:
                    ui.screenshot("step-error")
                except Exception:
                    pass
        finally:
            browser.close()
    return ui, error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("steps", type=Path, help="a .py file defining run(ui)")
    parser.add_argument("--out", type=Path, default=None, help="where screenshots go (default: a temp dir)")
    parser.add_argument("--port", type=int, default=0, help="preview server port (default: a free one)")
    parser.add_argument("--url", default="", help="check this running server instead of starting a preview")
    parser.add_argument("--user", default=None, help="user to log in as (default: the one with most chats)")
    parser.add_argument("--password", default=PREVIEW_PASSWORD, help="password, with --url")
    args = parser.parse_args(argv)

    if args.url:
        try:
            port = urllib.parse.urlparse(args.url).port
        except ValueError:
            port = None
        if port is None or port == 5210:
            print("setup error: --url needs an explicit port, and never 5210 (the user's live server)", file=sys.stderr)
            return 2
    # SIGTERM (bash_stop, a cancelled run) and SIGHUP must still run the finally
    # below: the server runs in its own session and would survive us, holding the
    # real encryption key, next to a DB copy with a known password.
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, _exit_on_signal)
    run_steps = _load_steps(args.steps.resolve())
    out_dir = (args.out or Path(tempfile.mkdtemp(prefix="ui-check-shots-"))).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="ui-check-"))
    proc = None
    try:
        if args.url:
            base_url, username = args.url.rstrip("/"), args.user or "admin"
        else:
            username = _prepare_db_copy(tmp, args.user)
            port = args.port or _free_port()
            base_url = f"http://127.0.0.1:{port}"
            proc = _start_server(tmp, port)
        _wait_until_up(base_url, proc)
        token = _login(base_url, username, args.password)
        ui, error = _run_browser(run_steps, base_url, token, out_dir)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        _print_log_tail(tmp)
        return 2
    except Exception:
        print("setup error:\n" + traceback.format_exc(limit=3), file=sys.stderr)
        _print_log_tail(tmp)
        return 2
    finally:
        if proc is not None:
            _stop_server(proc)
        shutil.rmtree(tmp, ignore_errors=True)

    results = ui.results if ui else []
    for ok, label in results:
        print(("PASS " if ok else "FAIL ") + label)
    if error:
        print("ERROR " + error)
    if not results and not error:
        print("ERROR no expectations: add at least one ui.expect_* call")
    for shot in ui.screenshots if ui else []:
        print(f"screenshot {shot}")
    passed = bool(results) and all(ok for ok, _ in results) and not error
    print("RESULT " + ("PASS" if passed else "FAIL") + f" ({sum(ok for ok, _ in results)}/{len(results)})")
    return 0 if passed else 1


def _exit_on_signal(signum, _frame) -> None:
    raise SystemExit(f"stopped by signal {signum}")


def _print_log_tail(tmp: Path, lines: int = 25) -> None:
    log = tmp / "server.log"
    if log.exists():
        tail = log.read_text(errors="replace").splitlines()[-lines:]
        print("--- preview server log (tail) ---\n" + "\n".join(tail), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())

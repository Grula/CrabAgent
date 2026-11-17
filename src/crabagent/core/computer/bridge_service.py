"""Platform-neutral collaboration browser bridge service.

This module provides the collaboration-browser HTTP bridge on top of
Playwright so the collaboration browser tools work in any deployment.

The wire contract is:

* ``POST /`` with ``Authorization: Bearer <token>``;
* envelope ``{protocol_version, runtime_id, task_id, trace_id, command, payload}``;
* ``200 {result: ...}`` on success and ``400 {detail: ...}`` on failure.

It runs its own asyncio event loop in a daemon thread so it never blocks or
interferes with the FastAPI/uvicorn loop in ``serve/app.py``.
"""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from aiohttp import web

from crabagent.core.agent.tools.browser_dom import (
    format_elements_for_llm,
    label_page_elements,
)
from crabagent.core.computer.url_policy import (
    LocalPreviewPolicy,
    allowed_network_url,
    normalize_browser_url,
)
from crabagent.core.config import settings

logger = logging.getLogger(__name__)

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    pass

START_URL = "https://www.google.com/"
MAX_BODY_BYTES = 100_000
MAX_LABELS = 80
ACTION_ID_TTL_SECONDS = 15 * 60
MAX_ACTION_IDS = 4096
CONFIRM_TTL_SECONDS = 120
VIEWPORT = {"width": 1280, "height": 800}

# Visibility (screencast) endpoints — a pollable JPEG frame plus a live MJPEG
# stream, matching the wire contract of the now-removed browser_bridge demo.
MJPEG_BOUNDARY = "crabagent-frame"
FRAME_INTERVAL_SECONDS = 1.0 / 10.0  # ~10 fps
FRAME_JPEG_QUALITY = 70

# Dark stand-in for the white about:blank page, so a freshly launched (or
# deliberately blank) browser never captures/displays a white canvas.
BLANK_PAGE_HTML = "<html><body style='background:#0d1117;margin:0'></body></html>"

# The envelope regex is deliberately loose (36 hex/dash characters); the
# action_id regex is a strict UUID v1-8.
TASK_ID_RE = re.compile(r"^[0-9a-f-]{36}$", re.IGNORECASE)
ACTION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# The person using the web viewer. Their commands never bind the browser to a
# task, are never refused because an agent task holds it, and are not paused by
# ``stop`` (stop pauses the agent, not the person). Only the viewer's own controls.
USER_TASK_ID = "00000000-0000-0000-0000-000000000000"
USER_COMMANDS = frozenset({"authorize_local_preview", "navigate", "back", "forward", "reload"})

ALLOWED_COMMANDS = frozenset(
    {
        "status",
        "stop",
        "resume",
        "release",
        "authorize_local_preview",
        "navigate",
        "back",
        "forward",
        "reload",
        "observe",
        "computer_observe",
        "screenshot",
        "click",
        "commit_click",
        "point",
        "commit_point",
        "type",
        "scroll",
        "select",
        "press_key",
        "wait_for",
    }
)

MUTATING_COMMANDS = frozenset(
    {
        "navigate",
        "back",
        "forward",
        "reload",
        "click",
        "commit_click",
        "point",
        "commit_point",
        "type",
        "scroll",
        "select",
        "press_key",
    }
)

# These two regexes implement isSensitiveInput and the click risk classifier.
# They are duplicated in the injected page JS (below) so the in-page target
# inspection behaves identically.
_SENSITIVE_SOURCE = (
    r"/password|passcode|otp|one.time|verification|captcha|recaptcha|hcaptcha|"
    r"human.challenge|cvv|card|credit|bank|iban|security|ssn|passport|id.card|"
    # CJK variants of the same labels, written as escapes so the source stays ASCII.
    r"\u94f6\u884c\u5361|\u5bc6\u7801|\u9a8c\u8bc1\u7801|\u6821\u9a8c\u7801|"
    r"\u652f\u4ed8|\u8eab\u4efd\u8bc1|\u4eba\u673a\u9a8c\u8bc1/i"
)
_RISKY_CLICK_SOURCE = (
    r"/pay|payment|purchase|order|delete|remove|send|submit|transfer|checkout|"
    # CJK variants, written as escapes so the source stays ASCII.
    r"publish|account|security|\u4ed8\u6b3e|\u652f\u4ed8|\u4e0b\u5355|\u5220\u9664|"
    r"\u53d1\u9001|\u63d0\u4ea4|\u8f6c\u8d26|\u53d1\u5e03|\u8d26\u53f7|\u5b89\u5168/i"
)

_IS_SENSITIVE_JS = (
    "function isSensitiveInput(type, label) {"
    "  return ['password','hidden','file'].includes(type) || "
    + _SENSITIVE_SOURCE
    + ".test(label);"
    "}"
)

# Injected into the page to gather a redacted text snapshot + fingerprints for
# the elements labelled by browser_dom.label_page_elements (data-crab-idx).
_SNAPSHOT_JS = r"""
() => {
  const fingerprints = {};
  document.querySelectorAll('[data-crab-idx]').forEach((el) => {
    const idx = el.getAttribute('data-crab-idx');
    const type = String(el.type || '').toLowerCase();
    const rawLabel = String(el.innerText || el.placeholder || el.getAttribute('aria-label') || el.name || '').slice(0, 100);
    fingerprints[idx] = [el.tagName.toLowerCase(), type, el.id, el.name, rawLabel].join('|').slice(0, 240);
  });
  const clone = document.body?.cloneNode(true);
  clone?.querySelectorAll('input, textarea, select, [contenteditable], [data-sensitive], [aria-live], script, style')
    .forEach((node) => node.replaceWith(document.createTextNode('[redacted]')));
  const bodyText = String(clone?.textContent || '').replace(/\s+/g, ' ').slice(0, 5000);
  return {
    url: location.href,
    title: document.title,
    text: bodyText,
    viewport: { width: innerWidth, height: innerHeight, scrollY: scrollY },
    fingerprints: fingerprints
  };
}
"""


def is_sensitive_input(type_: str, label: str) -> bool:
    if type_ in ("password", "hidden", "file"):
        return True
    return re.search(_SENSITIVE_SOURCE, label or "", re.IGNORECASE) is not None


def _is_risky_click(label_context: str) -> bool:
    return re.search(_RISKY_CLICK_SOURCE, label_context or "", re.IGNORECASE) is not None


class BridgeError(Exception):
    """An error that must be returned to the caller as ``400 {detail: ...}``."""


class BrowserBridgeService:
    def __init__(self, headless: bool | None = None) -> None:
        if headless is None:
            raw = os.environ.get("CRAB_BROWSER_HEADLESS", "true")
            headless = raw.lower() not in ("0", "false", "no", "off")
        self._headless = headless

        self.token: str = secrets.token_hex(32)
        self.port: int | None = None

        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._runner: web.AppRunner | None = None
        self._error: BaseException | None = None

        # Playwright state
        self._playwright = None
        self._context = None
        self._page = None

        # Collaboration-browser coordination state.
        self.page_version = 0
        self.frame_seq = 0
        self.observation: dict[str, Any] | None = None
        self.task_id: str | None = None
        self.stopped = False
        self.stop_epoch = 0
        self._queue_lock = asyncio.Lock()
        self.pending: dict[str, dict[str, Any]] = {}
        self.action_ids: dict[str, float] = {}

        allow_all = os.environ.get("CRAB_ALLOW_LOOPBACK_PREVIEWS", "").strip().lower() in ("1", "true", "yes", "on")
        self.local_preview_policy = LocalPreviewPolicy(
            forbidden_ports=lambda: self._forbidden_ports(),
            allow_all_loopback=allow_all,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def _forbidden_ports(self) -> list[int]:
        ports: list[int] = []
        # Fall back to the configured port when the server was started some other
        # way (e.g. uvicorn directly) and CRAB_APP_PORT was never exported.
        serve_port = os.environ.get("CRAB_APP_PORT") or str(settings.serve_port)
        if serve_port:
            try:
                ports.append(int(serve_port))
            except ValueError:
                pass
        if self.port:
            ports.append(self.port)
        return ports

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.port is not None

    def start(self, timeout: float = 15.0) -> "BrowserBridgeService":
        if self.is_running:
            return self
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="crab-collab-browser-bridge", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Browser bridge service failed to start")
        if self._error is not None:
            raise RuntimeError(f"Browser bridge service failed to start: {self._error}")
        return self

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self.port = None

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception as exc:  # pragma: no cover - surfaced via start()
            self._error = exc
            self._ready.set()
            logger.exception("Browser bridge service crashed")

    async def _serve(self) -> None:
        self._stop_event = asyncio.Event()
        app = web.Application()
        app.router.add_post("/", self._handle_http)
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/frame", self._handle_frame)
        app.router.add_get("/stream", self._handle_stream)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        sock.setblocking(False)
        self.port = sock.getsockname()[1]

        site = web.SockSite(self._runner, sock)
        await site.start()
        self._ready.set()
        logger.info("Browser bridge listening on 127.0.0.1:%s", self.port)

        await self._stop_event.wait()
        await site.stop()
        await self._runner.cleanup()
        await self._close_browser()
        self._runner = None

    # ------------------------------------------------------------------ #
    # HTTP envelope
    # ------------------------------------------------------------------ #
    async def _handle_http(self, request: web.Request) -> web.Response:
        if request.method != "POST" or not secrets.compare_digest(request.headers.get("Authorization", ""), f"Bearer {self.token}"):
            return web.json_response({"detail": "Unauthorized"}, status=401)
        try:
            raw = await request.read()
            if len(raw) > MAX_BODY_BYTES:
                return web.json_response({"detail": "Bridge request failed"}, status=400)
            body = json.loads(raw or b"{}")
            if (
                body.get("protocol_version") != 1
                or body.get("runtime_id") != "browser:collaboration"
                or not isinstance(body.get("task_id"), str)
                or not TASK_ID_RE.match(body["task_id"])
                or not isinstance(body.get("trace_id"), str)
                or not TASK_ID_RE.match(body["trace_id"])
            ):
                raise BridgeError("Invalid computer protocol or runtime binding")
            command = str(body.get("command") or "")
            payload = body.get("payload")
            if command not in ALLOWED_COMMANDS or not isinstance(payload, dict) or isinstance(payload, list):
                raise BridgeError("Invalid bridge command or payload")
        except BridgeError as exc:
            return web.json_response({"detail": str(exc)}, status=400)
        except Exception:
            return web.json_response({"detail": "Bridge request failed"}, status=400)

        try:
            result = await self._dispatch(command, payload, body["task_id"])
            return web.json_response({"result": result}, status=200)
        except BridgeError as exc:
            return web.json_response({"detail": str(exc)}, status=400)
        except Exception:
            logger.exception("Unhandled bridge error for command %r", command)
            return web.json_response({"detail": "Bridge request failed"}, status=400)

    # ------------------------------------------------------------------ #
    # Visibility endpoints (screencast): health, single JPEG frame, MJPEG
    # ------------------------------------------------------------------ #
    def _authorized(self, request: web.Request) -> bool:
        return secrets.compare_digest(
            request.headers.get("Authorization", ""),
            f"Bearer {self.token}",
        )

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"alive": True})

    async def _capture_jpeg(self) -> bytes:
        await self._ensure_browser()
        # The viewer polls every few hundred ms; while a slow page loads a
        # screenshot can stall, so give up quickly and let the next poll retry.
        data = await self._page.screenshot(
            type="jpeg", quality=FRAME_JPEG_QUALITY, full_page=False, timeout=5_000
        )
        if not data:
            raise RuntimeError("screenshot produced no bytes")
        return data

    async def _handle_frame(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"detail": "Unauthorized"}, status=401)
        try:
            data = await self._capture_jpeg()
        except BridgeError as exc:
            return web.json_response({"detail": str(exc)}, status=503)
        except Exception:
            logger.exception("Failed to capture visibility frame")
            return web.json_response({"detail": "capture failed"}, status=500)
        self.frame_seq += 1
        return web.Response(
            body=data,
            content_type="image/jpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Frame-Seq": str(self.frame_seq),
            },
        )

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.json_response({"detail": "Unauthorized"}, status=401)
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
                "Cache-Control": "no-store, no-transform",
            },
        )
        await response.prepare(request)
        try:
            while True:
                try:
                    data = await self._capture_jpeg()
                except Exception:
                    logger.exception("Failed to capture stream frame")
                    await asyncio.sleep(FRAME_INTERVAL_SECONDS)
                    continue
                self.frame_seq += 1
                part = (
                    f"--{MJPEG_BOUNDARY}\r\n"
                    "Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(data)}\r\n"
                    f"X-Frame-Seq: {self.frame_seq}\r\n"
                    "\r\n"
                ).encode("utf-8") + data + b"\r\n"
                try:
                    await response.write(part)
                except (ConnectionResetError, ConnectionAbortedError):
                    break
                await asyncio.sleep(FRAME_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        finally:
            try:
                await response.write_eof()
            except Exception:
                pass
        return response

    # ------------------------------------------------------------------ #
    # Dispatch + task binding + serialized queue
    # ------------------------------------------------------------------ #
    async def _dispatch(self, command: str, payload: dict[str, Any], task_id: str) -> dict[str, Any]:
        if command in ("status", "stop"):
            return await self._execute(command, payload, task_id, self.stop_epoch)

        queued_at_epoch = self.stop_epoch
        async with self._queue_lock:
            return await self._execute(command, payload, task_id, queued_at_epoch)

    async def _execute(
        self,
        command: str,
        payload: dict[str, Any],
        task_id: str,
        queued_at_epoch: int,
    ) -> dict[str, Any]:
        as_user = task_id == USER_TASK_ID
        if as_user:
            if command not in USER_COMMANDS:
                raise BridgeError("Invalid bridge command for the viewer")
        else:
            if self.task_id and self.task_id != task_id and command not in ("stop", "resume", "status"):
                raise BridgeError("TASK_MISMATCH: another task is using the collaboration browser")
            if queued_at_epoch != self.stop_epoch and command not in ("status", "stop", "resume"):
                raise BridgeError("STOPPED: action queued before stop")
            if not self.task_id and not self.stopped and command not in ("stop", "resume", "status", "release"):
                self.task_id = task_id

        if command in MUTATING_COMMANDS:
            action_id = payload.get("action_id")
            if not isinstance(action_id, str) or not ACTION_ID_RE.match(action_id):
                raise BridgeError("Invalid action_id")
            self._purge_action_ids()
            if action_id in self.action_ids:
                raise BridgeError("DUPLICATE_ACTION: do not retry; re-observe and reconcile")
            self.action_ids[action_id] = time.monotonic()
            if len(self.action_ids) > MAX_ACTION_IDS:
                self.action_ids.pop(next(iter(self.action_ids)))

        return await self._handle_command(command, payload, as_user=as_user)

    def _purge_action_ids(self) -> None:
        now = time.monotonic()
        for action_id in [key for key, ts in self.action_ids.items() if now - ts > ACTION_ID_TTL_SECONDS]:
            self.action_ids.pop(action_id, None)

    # ------------------------------------------------------------------ #
    # Observation bookkeeping
    # ------------------------------------------------------------------ #
    def _invalidate(self, reason: str = "") -> None:
        if reason:
            logger.debug("invalidate reason=%s", reason)
        self.page_version += 1
        self.observation = None
        self.pending.clear()

    async def _ensure_browser(self) -> None:
        if self._page is not None and not self._page.is_closed():
            return
        if not PLAYWRIGHT_AVAILABLE:
            raise BridgeError(
                "playwright is not installed. Install with: pip install 'crabagent[browser]' "
                "&& playwright install chromium"
            )

        proxy = ""
        try:
            from crabagent.core.proxy import resolve_category_proxy

            proxy = await resolve_category_proxy("browser")
        except Exception:
            logger.debug("Failed to resolve browser proxy", exc_info=True)

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        launch_kwargs: dict[str, Any] = {
            "headless": self._headless,
            "viewport": VIEWPORT,
            "args": ["--disable-dev-shm-usage"],
        }
        if os.geteuid() == 0:
            launch_kwargs["args"].append("--no-sandbox")
            logger.warning("Running as root; launching Chromium with --no-sandbox")
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}

        profile_dir = Path.home() / ".crabagent" / "collab_browser_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._context = await self._playwright.chromium.launch_persistent_context(
            str(profile_dir), **launch_kwargs
        )
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()
        await self._page.set_viewport_size(VIEWPORT)
        # Start on a dark blank page so the screencast never flashes white.
        await self._page.set_content(BLANK_PAGE_HTML)

    async def _close_browser(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:
            logger.debug("Failed to close browser context", exc_info=True)
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            logger.debug("Failed to stop playwright", exc_info=True)
        self._context = None
        self._page = None
        self._playwright = None

    async def _current_url(self) -> str:
        return self._page.url if self._page is not None else "about:blank"

    async def _current_viewport(self) -> dict[str, int]:
        if self._page is None:
            return {"width": 0, "height": 0}
        return await self._page.evaluate("() => ({ width: innerWidth, height: innerHeight })")

    async def _require_current_page_version(self, payload: dict[str, Any]) -> None:
        requested = payload.get("page_version")
        if not isinstance(requested, int) or requested != self.page_version:
            raise BridgeError("STALE_PAGE: observe the page again before acting")
        obs = self.observation
        if not obs or obs.get("page_version") != requested:
            raise BridgeError("STALE_PAGE: observe the page again before acting")
        if obs.get("url") != await self._current_url():
            raise BridgeError("STALE_PAGE: observe the page again before acting")
        if payload.get("observation_id") != obs.get("id"):
            raise BridgeError("STALE_PAGE: observe the page again before acting")
        if time.monotonic() - obs.get("captured_at", 0) > 30:
            raise BridgeError("STALE_PAGE: observe the page again before acting")
        viewport = await self._current_viewport()
        if obs["viewport"]["width"] != viewport["width"] or obs["viewport"]["height"] != viewport["height"]:
            raise BridgeError("STALE_PAGE: viewport changed; observe again")

    # ------------------------------------------------------------------ #
    # Command handlers
    # ------------------------------------------------------------------ #
    async def _handle_command(self, command: str, payload: dict[str, Any], *, as_user: bool = False) -> dict[str, Any]:
        if command == "release":
            self.task_id = None
            self._invalidate("task-release")
            return {"released": True}

        if command == "stop":
            self.stopped = True
            self.stop_epoch += 1
            self.local_preview_policy.clear()
            self.task_id = None
            self._invalidate("stop")
            return {"stopped": True}

        if command == "resume":
            self.stopped = False
            self.task_id = None
            self._invalidate("resume")
            return {"resumed": True}

        if self.stopped and not as_user and command not in ("status", "observe", "computer_observe", "screenshot"):
            raise BridgeError("STOPPED: browser actions are paused")

        if command == "status":
            await self._ensure_browser()
            return {
                "page_version": self.page_version,
                "url": await self._current_url(),
                "title": await self._page.title(),
                "loading": await self._page.evaluate('() => document.readyState !== "complete"'),
                "headless": self._headless,
            }

        if command == "authorize_local_preview":
            return self._authorize_local_preview(payload)

        if command == "navigate":
            return await self._navigate(payload)

        if command in ("back", "forward", "reload"):
            return await self._history(command)

        if command == "observe":
            return await self._observe()

        if command == "computer_observe":
            return await self._computer_observe()

        if command == "screenshot":
            return await self._computer_observe()

        if command in ("click", "commit_click"):
            return await self._click(command, payload)

        if command in ("point", "commit_point"):
            return await self._point(command, payload)

        if command == "type":
            return await self._type(payload)

        if command == "scroll":
            return await self._scroll(payload)

        if command == "select":
            return await self._select(payload)

        if command == "press_key":
            return await self._press_key(payload)

        if command == "wait_for":
            return await self._wait_for(payload)

        raise BridgeError("Unsupported collaboration browser command")

    def _authorize_local_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        # There is no native dialog on a headless backend, so the grant path
        # applies directly after the strict loopback origin validation above
        # (local_preview_origin + reserved port check). The origin is still
        # bounded to 30 minutes.
        try:
            origin = self.local_preview_policy.grant(
                str(payload.get("url") or ""), all_ports=bool(payload.get("all_ports"))
            )
        except ValueError as exc:
            raise BridgeError(str(exc)) from exc
        return {"status": "authorized", "origin": origin, "expires_in_seconds": 1800}

    async def _navigate(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_browser()
        raw_url = str(payload.get("url") or "")
        # The blank page is a legitimate internal destination and must be
        # reachable for the bridge self-test; everything else goes through the
        # URL policy (http/https only, no private/loopback targets).
        try:
            blank = raw_url.strip().lower() in ("about:blank", "about:blank/")
            if blank:
                target = "about:blank"
            else:
                target = await allowed_network_url(
                    normalize_browser_url(raw_url), self.local_preview_policy
                )
        except ValueError as exc:
            raise BridgeError(str(exc)) from exc
        self._invalidate("navigate")
        try:
            if blank:
                await self._page.set_content(BLANK_PAGE_HTML)
            else:
                await self._page.goto(target, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            self._invalidate("navigate")
            raise BridgeError(f"NAVIGATION_FAILED: {exc}") from exc
        return {
            "page_version": self.page_version,
            "url": self._page.url,
            "title": await self._page.title(),
        }

    async def _history(self, action: str) -> dict[str, Any]:
        """Browser history commands (back/forward/reload), mirroring _navigate."""
        await self._ensure_browser()
        self._invalidate(action)
        try:
            if action == "back":
                await self._page.go_back(timeout=30_000)
            elif action == "forward":
                await self._page.go_forward(timeout=30_000)
            else:
                await self._page.reload(timeout=30_000)
        except Exception as exc:
            self._invalidate(action)
            raise BridgeError(f"HISTORY_FAILED: {exc}") from exc
        return {
            "page_version": self.page_version,
            "url": self._page.url,
        }

    async def _snapshot(self) -> dict[str, Any]:
        page = self._page
        elements = await label_page_elements(page)
        data = await page.evaluate(_SNAPSHOT_JS)
        return {
            "elements": elements,
            "url": data["url"],
            "title": data["title"],
            "text": data["text"],
            "viewport": data["viewport"],
            "fingerprints": data["fingerprints"],
        }

    async def _observe(self) -> dict[str, Any]:
        await self._ensure_browser()
        snapshot: dict[str, Any] | None = None
        for attempt in range(2):
            snapshot = await self._snapshot()
            if snapshot["url"] == self._page.url:
                break
            if attempt == 0:
                logger.debug(
                    "Observation stale: snapshotUrl=%s liveUrl=%s",
                    snapshot["url"],
                    self._page.url,
                )
                await asyncio.sleep(0.3)
        if snapshot is None or snapshot["url"] != self._page.url:
            raise BridgeError(
                f"STALE_PAGE: navigation during observation (liveUrl={self._page.url})"
            )

        self.pending.clear()
        element_fingerprints: dict[str, str] = {}
        for el in snapshot["elements"]:
            idx = str(el.get("idx"))
            element_fingerprints[idx] = snapshot["fingerprints"].get(idx, "")
        observation = {
            "page_version": self.page_version,
            "url": snapshot["url"],
            "id": str(uuid.uuid4()),
            "captured_at": time.monotonic(),
            "captured_at_ms": int(time.time() * 1000),
            "elements": element_fingerprints,
            "viewport": snapshot["viewport"],
            "_descriptors": snapshot["elements"],
        }
        self.observation = observation

        elements_text = format_elements_for_llm(snapshot["elements"])
        return {
            "page_version": observation["page_version"],
            "url": observation["url"],
            "title": snapshot["title"],
            "text": snapshot["text"],
            "elements": snapshot["elements"],
            "elements_text": elements_text,
            "viewport": snapshot["viewport"],
            "protocol_version": 1,
            "runtime_id": "browser:collaboration",
            "surface_id": "browser:collaboration",
            "captured_at_ms": observation["captured_at_ms"],
            "observation_id": observation["id"],
        }

    async def _computer_observe(self) -> dict[str, Any]:
        snapshot = await self._observe()
        obs = self.observation
        image = await self._page.screenshot(type="jpeg", quality=75, full_page=False)
        if not image:
            raise BridgeError("CAPTURE_FAILED: screenshot is empty")
        if len(image) > 2_000_000:
            raise BridgeError("CAPTURE_FAILED: screenshot exceeds size limit")

        after = await self._page.evaluate(
            "() => ({ url: location.href, width: innerWidth, height: innerHeight, scrollY: scrollY })"
        )
        if obs is not self.observation or after["url"] != snapshot["url"]:
            raise BridgeError("STALE_PAGE: viewport changed during screenshot")
        viewport = snapshot["viewport"]
        if after["width"] != viewport["width"] or after["height"] != viewport["height"]:
            raise BridgeError("STALE_PAGE: viewport changed during screenshot")

        data_url = "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
        return {
            **snapshot,
            "screenshot": {
                "width": viewport["width"],
                "height": viewport["height"],
                "scale_x": 1.0,
                "scale_y": 1.0,
            },
            "mime": "image/jpeg",
            "data_url": data_url,
        }

    def _element_descriptor(self, index: int) -> dict[str, Any]:
        obs = self.observation
        if not obs:
            raise BridgeError("Element not found; observe again")
        for el in obs.get("_descriptors", []):
            if el.get("idx") == index:
                return el
        raise BridgeError("Element not found; observe again")

    def _click_target_js(self, index: int) -> str:
        return (
            "(async () => {\n"
            "  " + _IS_SENSITIVE_JS + "\n"
            f"  const el = document.querySelector('[data-crab-idx=\"{index}\"]');\n"
            "  if (!el) throw new Error('Element not found; observe again');\n"
            "  const type = String(el.type || '').toLowerCase();\n"
            "  const label = String(el.innerText || el.placeholder || el.getAttribute('aria-label') || el.name || '').slice(0, 100);\n"
            "  const tag = el.tagName.toLowerCase();\n"
            "  const fingerprint = [tag, type, el.id, el.name, label].join('|').slice(0, 240);\n"
            "  el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });\n"
            "  await new Promise((resolve) => {\n"
            "    let done = false;\n"
            "    const finish = () => { if (!done) { done = true; resolve(); } };\n"
            "    requestAnimationFrame(() => requestAnimationFrame(finish));\n"
            "    setTimeout(finish, 500);\n"
            "  });\n"
            "  const rect = el.getBoundingClientRect();\n"
            "  const x = Math.round(rect.left + rect.width / 2);\n"
            "  const y = Math.round(rect.top + rect.height / 2);\n"
            "  const top = document.elementFromPoint(x, y);\n"
            "  if (!rect.width || !rect.height || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight || (top !== el && !el.contains(top))) {\n"
            "    throw new Error('Element is covered or outside the viewport; observe again');\n"
            "  }\n"
            "  const context = String(el.closest('form')?.innerText || '').slice(0, 250);\n"
            "  const sensitive = isSensitiveInput(type, [el.getAttribute('aria-label'), el.name, el.id, el.autocomplete, label, context].join(' '));\n"
            "  const href = el.closest('a[href]')?.href;\n"
            "  let crossOrigin = false;\n"
            "  try { crossOrigin = Boolean(href && new URL(href).origin !== location.origin); } catch (e) { crossOrigin = true; }\n"
            "  const form = el.closest('form');\n"
            "  const submit = (Boolean(form) && el.matches('button,input') && type === 'submit') || (tag === 'button' && !el.hasAttribute('type') && Boolean(form));\n"
            "  return { index: " + str(index) + ", label, fingerprint, context, sensitive, crossOrigin, submit, x, y, url: location.href, title: document.title };\n"
            "})()"
        )

    async def _click(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_browser()
        await self._require_current_page_version(payload)
        if command == "commit_click" and not payload.get("pending_action_id"):
            raise BridgeError("APPROVAL_INVALID")
        index = payload.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 1 or index > MAX_LABELS:
            raise BridgeError("Invalid element index")

        observation = self.observation
        if observation is None:
            raise BridgeError("STALE_PAGE: observe the page again before acting")
        target = await self._page.evaluate(self._click_target_js(index))
        if observation is not self.observation or observation["elements"].get(str(index)) != target["fingerprint"]:
            raise BridgeError("STALE_PAGE: target changed; observe again")
        if target["url"] != observation["url"]:
            raise BridgeError("STALE_PAGE: target changed; observe again")
        if target["sensitive"]:
            raise BridgeError("SENSITIVE_INPUT")

        risky = _is_risky_click(target["label"] + " " + target["context"]) or target["crossOrigin"] or target["submit"]
        digest = hashlib.sha256(
            json.dumps(
                {
                    "index": index,
                    "fingerprint": target["fingerprint"],
                    "origin": self._origin_of(target["url"]),
                    "observation": observation["id"],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        if self.stopped:
            raise BridgeError("STOPPED")
        if command == "click" and risky:
            pending_action_id = str(uuid.uuid4())
            self.pending[pending_action_id] = {
                "digest": digest,
                "index": index,
                "observation": observation,
                "expires": time.monotonic() + CONFIRM_TTL_SECONDS,
            }
            return {
                "confirmation_required": True,
                "pending_action_id": pending_action_id,
                "index": index,
                "label": target["label"],
                "url": target["url"],
                "page_version": self.page_version,
            }
        if command == "commit_click":
            pending_id = str(payload.get("pending_action_id") or "")
            pending = self.pending.pop(pending_id, None)
            if (
                not pending
                or pending["expires"] < time.monotonic()
                or pending["index"] != index
                or pending["observation"] is not observation
                or pending["digest"] != digest
                or not risky
            ):
                raise BridgeError("APPROVAL_INVALID: action changed, expired or already used")
        elif risky:
            raise BridgeError("CONFIRMATION_REQUIRED")

        if await self._current_url() != target["url"] or observation is not self.observation or self.stopped:
            raise BridgeError("STALE_PAGE")

        await self._page.mouse.click(target["x"], target["y"])
        self.observation = None
        self.pending.clear()
        return {
            "clicked": index,
            "method": "native_mouse",
            "point": {"x": target["x"], "y": target["y"]},
            "url": target["url"],
            "title": target["title"],
            "page_version": self.page_version,
        }

    @staticmethod
    def _origin_of(url: str) -> str:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _point_target_js(self, x: int, y: int) -> str:
        return (
            "(() => {\n"
            "  " + _IS_SENSITIVE_JS + "\n"
            f"  const el = document.elementFromPoint({x}, {y});\n"
            "  if (!el) throw new Error('Point has no target');\n"
            "  const tag = el.tagName.toLowerCase();\n"
            "  const label = String(el.innerText || el.getAttribute('aria-label') || el.title || '').slice(0, 100);\n"
            "  const form = String(el.closest('form')?.innerText || '').slice(0, 250);\n"
            "  const input = el.closest('input,textarea,select,[contenteditable]');\n"
            "  return { tag, label, form, type: input?.type || '', url: location.href,\n"
            "    sensitive: isSensitiveInput(input?.type || '', [label, form, input?.name, input?.id, input?.autocomplete, input?.getAttribute('aria-label')].join(' ')),\n"
            "    editable: Boolean(input) };\n"
            "})()"
        )

    async def _point(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_browser()
        await self._require_current_page_version(payload)
        if command == "commit_point" and not payload.get("pending_action_id"):
            raise BridgeError("APPROVAL_INVALID")

        observation = self.observation
        if observation is None:
            raise BridgeError("STALE_PAGE: observe the page again before acting")
        x = payload.get("x")
        y = payload.get("y")
        kind = str(payload.get("kind") or "click")
        if (
            not isinstance(x, int) or isinstance(x, bool)
            or not isinstance(y, int) or isinstance(y, bool)
            or kind not in ("click", "double_click", "move", "drag", "scroll")
        ):
            raise BridgeError("Invalid point action")

        viewport = VIEWPORT
        if x < 0 or y < 0 or x >= viewport["width"] or y >= viewport["height"]:
            raise BridgeError("POINT_OUTSIDE_VIEWPORT")

        details = await self._page.evaluate(self._point_target_js(x, y))
        if observation is not self.observation or details["url"] != await self._current_url():
            raise BridgeError("STALE_PAGE")
        if details["sensitive"] or details["editable"]:
            raise BridgeError("SENSITIVE_OR_EDITABLE_POINT")

        if kind == "drag":
            x2 = payload.get("x2")
            y2 = payload.get("y2")
            if (
                not isinstance(x2, int) or isinstance(x2, bool)
                or not isinstance(y2, int) or isinstance(y2, bool)
                or x2 < 0 or y2 < 0
                or x2 >= viewport["width"] or y2 >= viewport["height"]
            ):
                raise BridgeError("Invalid drag endpoint")

        digest = hashlib.sha256(
            json.dumps(
                {
                    "kind": kind,
                    "x": x,
                    "y": y,
                    "x2": payload.get("x2"),
                    "y2": payload.get("y2"),
                    "amount": payload.get("amount"),
                    "observation": observation["id"],
                    "details": details,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        needs_confirmation = kind in ("click", "double_click", "drag")
        if command == "point" and needs_confirmation:
            pending_action_id = str(uuid.uuid4())
            self.pending[pending_action_id] = {
                "digest": digest,
                "observation": observation,
                "expires": time.monotonic() + CONFIRM_TTL_SECONDS,
            }
            return {
                "confirmation_required": True,
                "pending_action_id": pending_action_id,
                "label": details["label"] or "Visual target",
                "url": details["url"],
                "page_version": self.page_version,
            }
        if command == "commit_point":
            pending_id = str(payload.get("pending_action_id") or "")
            pending = self.pending.pop(pending_id, None)
            if (
                not needs_confirmation
                or not pending
                or pending["expires"] < time.monotonic()
                or pending["observation"] is not observation
                or pending["digest"] != digest
            ):
                raise BridgeError("APPROVAL_INVALID")

        if self.stopped or observation is not self.observation or details["url"] != await self._current_url():
            raise BridgeError("STOPPED_OR_STALE")

        if kind == "scroll":
            amount = payload.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or abs(amount) > 2000:
                raise BridgeError("Invalid scroll")
            await self._page.mouse.move(x, y)
            await self._page.mouse.wheel(0, -amount)
        elif kind == "move":
            await self._page.mouse.move(x, y)
        else:
            if kind == "drag":
                x2 = payload.get("x2")
                y2 = payload.get("y2")
                await self._page.mouse.move(x, y)
                await asyncio.sleep(0.04)
                await self._page.mouse.down()
                await asyncio.sleep(0.04)
                await self._page.mouse.move(x + (1 if x2 > x else -1), y + (1 if y2 > y else -1))
                await asyncio.sleep(0.04)
                for i in range(2, 11):
                    release_x = round(x + (x2 - x) * i / 10)
                    release_y = round(y + (y2 - y) * i / 10)
                    await self._page.mouse.move(release_x, release_y)
                    await asyncio.sleep(0.03)
                await asyncio.sleep(0.04)
                await self._page.mouse.up()
            else:
                count = 2 if kind == "double_click" else 1
                await self._page.mouse.move(x, y)
                for _ in range(count):
                    await self._page.mouse.down()
                    await self._page.mouse.up()

        self.observation = None
        self.pending.clear()
        return {"performed": kind, "point": {"x": x, "y": y}, "page_version": self.page_version}

    def _type_target_js(self, index: int) -> str:
        return (
            "(() => {\n"
            "  " + _IS_SENSITIVE_JS + "\n"
            f"  const el = document.querySelector('[data-crab-idx=\"{index}\"]');\n"
            "  if (!el) throw new Error('Element not found; observe again');\n"
            "  const type = String(el.type || '').toLowerCase();\n"
            "  const label = String(el.placeholder || el.getAttribute('aria-label') || el.name || '');\n"
            "  if (isSensitiveInput(type, [label, el.id, el.autocomplete, el.closest('form')?.innerText].join(' '))) throw new Error('SENSITIVE_INPUT: AI cannot type into this field');\n"
            "  if (!el.matches('input,textarea,[contenteditable]:not([contenteditable=\"false\"])')) throw new Error('Not an editable field');\n"
            "  const textLabel = String(el.innerText || el.placeholder || el.getAttribute('aria-label') || el.name || '').slice(0, 100);\n"
            "  const fingerprint = [el.tagName.toLowerCase(), type, el.id, el.name, textLabel].join('|').slice(0, 240);\n"
            "  el.focus();\n"
            "  if (el.isContentEditable) {\n"
            "    const range = document.createRange();\n"
            "    range.selectNodeContents(el);\n"
            "    const selection = window.getSelection();\n"
            "    selection.removeAllRanges();\n"
            "    selection.addRange(range);\n"
            "  } else {\n"
            "    el.select();\n"
            "  }\n"
            "  return { fingerprint, url: location.href };\n"
            "})()"
        )

    async def _type(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_browser()
        await self._require_current_page_version(payload)
        index = payload.get("index")
        text = str(payload.get("text") or "")
        if not isinstance(index, int) or isinstance(index, bool) or index < 1 or index > MAX_LABELS or len(text) > 10_000:
            raise BridgeError("Invalid type request")

        observation = self.observation
        if observation is None:
            raise BridgeError("STALE_PAGE: observe the page again before acting")
        target = await self._page.evaluate(self._type_target_js(index))
        if (
            observation is not self.observation
            or observation["elements"].get(str(index)) != target["fingerprint"]
            or target["url"] != await self._current_url()
            or self.stopped
        ):
            raise BridgeError("STALE_PAGE")
        if text:
            await self._page.keyboard.insert_text(text)
        self.observation = None
        self.pending.clear()
        return {"typed": index, "url": target["url"], "page_version": self.page_version}

    async def _scroll(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_browser()
        await self._require_current_page_version(payload)
        amount = payload.get("amount")
        if not isinstance(amount, int) or isinstance(amount, bool) or abs(amount) > 2000:
            raise BridgeError("Invalid scroll")
        if self.stopped:
            raise BridgeError("STOPPED")
        outcome = await self._page.evaluate(
            f"() => {{ window.scrollBy(0, {amount}); return {{ scrollY: window.scrollY, url: location.href }}; }}"
        )
        self.observation = None
        self.pending.clear()
        return {**outcome, "page_version": self.page_version}

    def _select_target_js(self, index: int, value: str) -> str:
        return (
            "(() => {\n"
            "  " + _IS_SENSITIVE_JS + "\n"
            f"  const el = document.querySelector('[data-crab-idx=\"{index}\"]');\n"
            "  if (!el || el.tagName !== 'SELECT') throw new Error('Element not found or not select');\n"
            "  const label = String(el.innerText || el.placeholder || el.getAttribute('aria-label') || el.name || '').slice(0, 100);\n"
            "  const fingerprint = [el.tagName.toLowerCase(), String(el.type || '').toLowerCase(), el.id, el.name, label].join('|').slice(0, 240);\n"
            f"  const option = Array.from(el.options).find((item) => item.value === {json.dumps(value)} || item.text === {json.dumps(value)});\n"
            "  if (!option) throw new Error('Option not found');\n"
            "  return { fingerprint, sensitive: isSensitiveInput('', [el.getAttribute('aria-label'), el.name, el.id, el.autocomplete, option.text, el.closest('form')?.innerText].join(' ')), url: location.href };\n"
            "})()"
        )

    def _select_apply_js(self, index: int, value: str) -> str:
        return (
            "(() => {\n"
            f"  const el = document.querySelector('[data-crab-idx=\"{index}\"]');\n"
            "  if (!el || !el.isConnected) throw new Error('STALE_PAGE');\n"
            f"  const option = Array.from(el.options).find((item) => item.value === {json.dumps(value)} || item.text === {json.dumps(value)});\n"
            "  if (!option) throw new Error('STALE_PAGE');\n"
            "  el.value = option.value;\n"
            "  el.dispatchEvent(new Event('input', { bubbles: true }));\n"
            "  el.dispatchEvent(new Event('change', { bubbles: true }));\n"
            f"  return {{ selected: {index}, url: location.href }};\n"
            "})()"
        )

    async def _select(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_browser()
        await self._require_current_page_version(payload)
        index = payload.get("index")
        value = str(payload.get("value") or "")
        if not isinstance(index, int) or isinstance(index, bool) or index < 1 or index > MAX_LABELS or len(value) > 1000:
            raise BridgeError("Invalid select request")

        observation = self.observation
        if observation is None:
            raise BridgeError("STALE_PAGE: observe the page again before acting")
        target = await self._page.evaluate(self._select_target_js(index, value))
        if target["sensitive"]:
            raise BridgeError("SENSITIVE_INPUT")
        if (
            observation is not self.observation
            or observation["elements"].get(str(index)) != target["fingerprint"]
            or target["url"] != await self._current_url()
            or self.stopped
        ):
            raise BridgeError("STALE_PAGE")
        outcome = await self._page.evaluate(self._select_apply_js(index, value))
        self.observation = None
        self.pending.clear()
        return {**outcome, "page_version": self.page_version}

    async def _press_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_browser()
        await self._require_current_page_version(payload)
        key = str(payload.get("key") or "")
        if key not in ("Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"):
            raise BridgeError("Unsupported key")
        if key == "Enter":
            target = await self._page.evaluate(
                "(() => { const el = document.activeElement; return { tag: el?.tagName, type: el?.type || '', label: String(el?.outerHTML || '').slice(0, 300), form: String(el?.closest('form')?.innerText || '').slice(0, 250) }; })()"
            )
            if target["tag"] != "INPUT" or is_sensitive_input(target["type"], target["label"]) or _is_risky_click(target["form"]):
                raise BridgeError("CONFIRMATION_REQUIRED: use a reviewed element click instead")
        if self.stopped:
            raise BridgeError("STOPPED")
        await self._page.keyboard.press(key)
        self.observation = None
        self.pending.clear()
        return {"pressed": key, "url": await self._current_url(), "page_version": self.page_version}

    async def _wait_for(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_browser()
        try:
            timeout_ms = int(payload.get("timeout_ms") or 10_000)
        except (TypeError, ValueError):
            timeout_ms = 10_000
        timeout_ms = max(100, min(30_000, timeout_ms))
        text = str(payload.get("text") or "")[:500]
        url_includes = str(payload.get("url_includes") or "")[:500]
        started = time.monotonic()
        while time.monotonic() - started < timeout_ms / 1000.0:
            if self.stopped:
                raise BridgeError("STOPPED")
            matched = await self._page.evaluate(
                "() => ({"
                f" text: {json.dumps(bool(text))} ? document.body?.innerText?.includes({json.dumps(text)}) : false,"
                f" url: {json.dumps(bool(url_includes))} ? location.href.includes({json.dumps(url_includes)}) : false,"
                ' loading: document.readyState !== "complete"'
                " })"
            )
            if (not text or matched["text"]) and (not url_includes or matched["url"]) and not matched["loading"]:
                return {
                    "matched": True,
                    "page_version": self.page_version,
                    "url": self._page.url,
                    "title": await self._page.title(),
                }
            await asyncio.sleep(0.2)
        raise BridgeError("WAIT_TIMEOUT: requested page condition was not met")


# --------------------------------------------------------------------- #
# Module-level singleton used by serve/app.py
# --------------------------------------------------------------------- #
_bridge: BrowserBridgeService | None = None
_bridge_lock = threading.Lock()

BRIDGE_LOCK_FILENAME = "browser_bridge.lock"
_bridge_lock_fh = None


def _lock_path() -> Path:
    p = Path.home() / ".crabagent" / BRIDGE_LOCK_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_bridge_lock():
    fh = _lock_path().open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        raw = fh.read().strip()
        pid = int(raw) if raw.isdigit() else None
        fh.close()
        if pid is not None and _pid_alive(pid):
            raise RuntimeError(f"collaboration browser bridge already running (PID {pid})")
        logger.warning("stale browser bridge lock (pid=%s); breaking", pid)
        try:
            _lock_path().unlink()
        except FileNotFoundError:
            pass
        fh = _lock_path().open("a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def start_browser_bridge() -> BrowserBridgeService:
    """Start the bridge and publish its port/token for collaboration_browser.py."""
    global _bridge, _bridge_lock_fh
    with _bridge_lock:
        if _bridge is not None and _bridge.is_running:
            return _bridge
        _bridge_lock_fh = _acquire_bridge_lock()
        _bridge = BrowserBridgeService()
        _bridge.start()
        os.environ["CRAB_COLLAB_BROWSER_PORT"] = str(_bridge.port)
        os.environ["CRAB_COLLAB_BROWSER_TOKEN"] = _bridge.token
        return _bridge


def stop_browser_bridge() -> None:
    global _bridge, _bridge_lock_fh
    with _bridge_lock:
        if _bridge is not None:
            _bridge.stop()
            _bridge = None
        try:
            if _bridge_lock_fh is not None:
                fcntl.flock(_bridge_lock_fh.fileno(), fcntl.LOCK_UN)
                _bridge_lock_fh.close()
                _bridge_lock_fh = None
        finally:
            _bridge_lock_fh = None

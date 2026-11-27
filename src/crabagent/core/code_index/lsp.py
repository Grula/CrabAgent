"""Precise Python navigation through basedpyright's language server.

``code_search kind="reference"`` and ``code_nav`` ask the language server where a
name is defined and used, instead of matching names: ``registry.execute`` and
``db.execute`` are different methods to it. One server per workspace stays
running between calls and is closed after :data:`IDLE_SECONDS`.

Three details matter in practice (each found by measuring):
* the server asks for its settings (``workspace/configuration``); the answer
  adds ``src/`` to the import roots, so src-layout packages resolve without an
  installed package or a config file in the user's project;
* the first query of a fresh server comes back before the workspace is
  analysed (1 reference instead of 44): it is asked twice;
* agents change files behind the server's back, so before each query the
  changed ``.py`` files are reported (``workspace/didChangeWatchedFiles``).

Everything degrades to ``None`` (no server installed, a crash, a timeout); the
callers then fall back to name matching and say so.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

IDLE_SECONDS = 600
REQUEST_TIMEOUT = 45
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
              ".ruff_cache", "dist", "build", ".crabagent", ".tox", "site-packages"}
# LSP file-change kinds.
_CREATED, _CHANGED, _DELETED = 1, 2, 3


@dataclass(frozen=True)
class Location:
    path: Path
    line: int  # 1-based
    column: int  # 0-based


def server_command() -> list[str] | None:
    for name in ("basedpyright-langserver", "pyright-langserver"):
        found = shutil.which(name)
        if found:
            return [found, "--stdio"]
    return None


def _uri_to_path(uri: str) -> Path:
    return Path(unquote(urlparse(uri).path))


class _Client:
    """A minimal JSON-RPC over stdio client for one language server process."""

    def __init__(self, proc: asyncio.subprocess.Process, settings: dict) -> None:
        self.proc = proc
        self.settings = settings
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader = asyncio.create_task(self._read())

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None and not self._reader.done()

    def _send(self, message: dict) -> None:
        data = json.dumps(message).encode()
        assert self.proc.stdin is not None
        self.proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)

    async def _read(self) -> None:
        stream = self.proc.stdout
        assert stream is not None
        try:
            while True:
                length = 0
                while True:
                    line = await stream.readline()
                    if not line:
                        return
                    line = line.strip()
                    if not line:
                        break
                    key, _, value = line.decode("ascii", "replace").partition(":")
                    if key.lower() == "content-length":
                        length = int(value.strip())
                message = json.loads(await stream.readexactly(length))
                if "method" in message and "id" in message:
                    self._send({"jsonrpc": "2.0", "id": message["id"], "result": self._answer(message)})
                elif "id" in message:
                    future = self._pending.pop(message["id"], None)
                    if future and not future.done():
                        future.set_result(message)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            return
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("language server exited"))

    def _answer(self, message: dict):
        """Requests the server sends us: settings, capability registration, progress tokens."""
        if message.get("method") == "workspace/configuration":
            items = (message.get("params") or {}).get("items") or []
            return [
                self.settings if (item.get("section") or "").endswith("analysis") else {"analysis": self.settings}
                for item in items
            ]
        return None

    async def request(self, method: str, params: dict, timeout: float = REQUEST_TIMEOUT):
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = future
        self._send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params})
        await self.proc.stdin.drain()
        reply = await asyncio.wait_for(future, timeout)
        if "error" in reply:
            raise RuntimeError(reply["error"].get("message", "language server error"))
        return reply.get("result")

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.request("shutdown", {}, timeout=3), 4)
            self.notify("exit", {})
        if self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.proc.wait(), 3)
        self._reader.cancel()


class PythonNavigator:
    """One running language server for one workspace."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._client: _Client | None = None
        self._lock = asyncio.Lock()
        self._mtimes: dict[Path, int] = {}
        self._opened: set[Path] = set()
        self._warm = False
        self.last_used = time.monotonic()

    def _settings(self) -> dict:
        extra = [str(self.workspace)]
        if (self.workspace / "src").is_dir():
            extra.insert(0, str(self.workspace / "src"))
        # No type checking: navigation only, so the server does as little work as it can.
        return {"extraPaths": extra, "typeCheckingMode": "off", "diagnosticMode": "openFilesOnly",
                "autoImportCompletions": False}

    def _scan(self) -> dict[Path, int]:
        found: dict[Path, int] = {}
        for dirpath, dirnames, filenames in os.walk(self.workspace):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if name.endswith((".py", ".pyi")):
                    path = Path(dirpath) / name
                    with contextlib.suppress(OSError):
                        found[path] = path.stat().st_mtime_ns
        return found

    async def _start(self) -> _Client | None:
        command = server_command()
        if command is None:
            return None
        proc = await asyncio.create_subprocess_exec(
            *command, cwd=str(self.workspace), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        client = _Client(proc, self._settings())
        root = self.workspace.as_uri()
        await client.request("initialize", {
            "processId": os.getpid(), "rootUri": root,
            "workspaceFolders": [{"uri": root, "name": self.workspace.name}],
            "capabilities": {"workspace": {"configuration": True, "didChangeWatchedFiles": {}}},
        })
        client.notify("initialized", {})
        self._mtimes = await asyncio.to_thread(self._scan)
        self._opened.clear()
        self._warm = False
        return client

    async def _sync(self, client: _Client) -> None:
        """Tell the server about .py files created, changed or deleted since the last call."""
        now = await asyncio.to_thread(self._scan)
        changes = [{"uri": p.as_uri(), "type": _CREATED} for p in now.keys() - self._mtimes.keys()]
        changes += [{"uri": p.as_uri(), "type": _DELETED} for p in self._mtimes.keys() - now.keys()]
        changes += [{"uri": p.as_uri(), "type": _CHANGED} for p in now.keys() & self._mtimes.keys()
                    if now[p] != self._mtimes[p]]
        self._mtimes = now
        if changes:
            client.notify("workspace/didChangeWatchedFiles", {"changes": changes})
            for change in changes:  # reopen with fresh text next time
                path = _uri_to_path(change["uri"])
                if path in self._opened:
                    client.notify("textDocument/didClose", {"textDocument": {"uri": change["uri"]}})
                    self._opened.discard(path)

    def _open(self, client: _Client, path: Path) -> None:
        if path in self._opened:
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        client.notify("textDocument/didOpen", {"textDocument": {
            "uri": path.as_uri(), "languageId": "python", "version": 1, "text": text}})
        self._opened.add(path)

    async def _query(self, method: str, path: Path, line: int, column: int) -> list[Location] | None:
        async with self._lock:
            self.last_used = time.monotonic()
            try:
                if self._client is None or not self._client.alive:
                    self._client = await self._start()
                    if self._client is None:
                        return None
                client = self._client
                await self._sync(client)
                self._open(client, path)
                params = {"textDocument": {"uri": path.as_uri()},
                          "position": {"line": line - 1, "character": column}}
                if method == "textDocument/references":
                    params["context"] = {"includeDeclaration": True}
                result = await client.request(method, params)
                if not self._warm:
                    # A fresh server answers before it has analysed the workspace.
                    self._warm = True
                    result = await client.request(method, params)
            except Exception:
                logger.debug("language server query failed for %s", path, exc_info=True)
                await self._reset()
                return None
        if isinstance(result, dict):
            result = [result]
        locations = []
        for item in result or []:
            uri = item.get("uri") or item.get("targetUri")
            span = item.get("range") or item.get("targetSelectionRange") or {}
            start = span.get("start") or {}
            if uri:
                locations.append(Location(_uri_to_path(uri), int(start.get("line", 0)) + 1,
                                          int(start.get("character", 0))))
        return locations

    async def references(self, path: Path, line: int, column: int) -> list[Location] | None:
        return await self._query("textDocument/references", path, line, column)

    async def definition(self, path: Path, line: int, column: int) -> list[Location] | None:
        return await self._query("textDocument/definition", path, line, column)

    async def _reset(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()

    async def close(self) -> None:
        async with self._lock:
            await self._reset()


_navigators: dict[Path, PythonNavigator] = {}
_reaper: asyncio.Task | None = None


async def _reap_idle() -> None:
    while _navigators:
        await asyncio.sleep(60)
        now = time.monotonic()
        for root, navigator in list(_navigators.items()):
            if now - navigator.last_used > IDLE_SECONDS and not navigator._lock.locked():
                _navigators.pop(root, None)
                await navigator.close()


def navigator_for(workspace: Path) -> PythonNavigator | None:
    """The workspace's navigator, or None when no Python language server is installed."""
    global _reaper
    if server_command() is None:
        return None
    root = workspace.resolve()
    navigator = _navigators.get(root)
    if navigator is None:
        navigator = _navigators[root] = PythonNavigator(root)
    if _reaper is None or _reaper.done():
        _reaper = asyncio.get_running_loop().create_task(_reap_idle())
    return navigator


async def close_all() -> None:
    """Stop every language server (server shutdown, tests)."""
    global _reaper
    for root in list(_navigators):
        navigator = _navigators.pop(root)
        await navigator.close()
    if _reaper is not None:
        _reaper.cancel()
        _reaper = None

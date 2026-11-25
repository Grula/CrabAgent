"""Read-only MCP server: exposes a small allowlist of CrabAgent tools over stdio.

``crabagent --mcp-server`` speaks newline-delimited JSON-RPC 2.0 on stdin/stdout
and implements the subset of MCP a client needs to browse and call tools:

    initialize, notifications/initialized, ping, tools/list, tools/call

Dependency-free on purpose: the binary is local-first, and the MCP python SDK is
optional (CrabAgent's *client* uses it, the server must not require it).

Safety model
------------
* ``READ_ONLY_TOOLS`` is an explicit, auditable allowlist (``read``, ``glob``,
  ``grep``). It is deliberately NOT derived from ``requires_permission``: a tool
  that forgets to set that flag must not silently become callable here.
* Every ``tools/call`` for a name outside the allowlist -- unknown, mutating or
  otherwise -- is refused *before* the registry executes anything, and the
  refusal is returned as a tool result with ``isError: true`` (consistent for
  unknown and mutating names: from the client's point of view it is a failed
  call, not a broken protocol).
* Path arguments are resolved against the workspace and refused when they land
  outside it (``../..``, absolute paths, symlinks pointing elsewhere).
* No handler outside the allowlist ever runs, so no network access, no
  subprocess and no file write is reachable through this server.

Nothing but JSON-RPC frames is ever written to stdout; logs go to stderr.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
from pathlib import Path
from typing import Any, Protocol

from crabagent import __version__

# Import the allowlisted tool modules so the registry has them even if this
# module is imported before ``crabagent.core`` wide registration happens.
import crabagent.core.agent.tools.glob  # noqa: F401
import crabagent.core.agent.tools.grep  # noqa: F401
import crabagent.core.agent.tools.read  # noqa: F401
from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.path_utils import resolve_tool_path
from crabagent.core.agent.tools.registry import ToolRegistry, registry

# Explicit allowlist. Do not derive this from ToolInfo.requires_permission.
READ_ONLY_TOOLS: tuple[str, ...] = ("read", "glob", "grep")
READ_ONLY_TOOL_SET = frozenset(READ_ONLY_TOOLS)

SERVER_NAME = "crabagent"
PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 2.0 error codes used here.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

# Arguments that name a filesystem location and must stay inside the workspace.
_PATH_ARGUMENTS = ("file_path", "path")

# Refusal text is identical for unknown and non-allowlisted tools, so clients get
# one predictable message: the tool is named and the server says it is read-only.
_REFUSAL = (
    "Error: refusing to run tool '{name}': this MCP server is read-only and exposes "
    "only: {allowed}."
)

_MAX_RESULT_CHARS = 200_000

logger = logging.getLogger(__name__)


class Stream(Protocol):
    def readline(self) -> str: ...

    def write(self, data: str) -> Any: ...

    def flush(self) -> Any: ...


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    """MCP CallToolResult shape."""
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _to_text(result: Any) -> str:
    """Render a tool result as MCP text content."""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
    if len(text) > _MAX_RESULT_CHARS:
        text = text[:_MAX_RESULT_CHARS] + "\n… (result truncated)"
    return text


def _looks_like_error(text: str) -> bool:
    """Tool handlers report failure by returning a string starting with 'Error'."""
    return text.lstrip().startswith("Error")


def _negotiate_protocol_version(requested: Any) -> str:
    """Echo the client's protocol version when it looks like a date, else ours."""
    if isinstance(requested, str) and len(requested) == 10 and requested[4] == "-" and requested[7] == "-":
        return requested
    return PROTOCOL_VERSION


class ReadOnlyMcpServer:
    """MCP server exposing only ``READ_ONLY_TOOLS`` inside one workspace."""

    def __init__(self, workspace: Path | str, tool_registry: ToolRegistry | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        # A private AgentContext keeps the tools inside the workspace. Its empty
        # tool_permissions means every tool stays on its own default (auto).
        self.context = AgentContext(workspace=self.workspace)
        self.registry = tool_registry or registry

    # ── protocol ────────────────────────────────────────────────────────
    async def handle_line(self, line: str) -> dict[str, Any] | None:
        """Parse one newline-delimited frame and answer it (or not)."""
        if not line.strip():
            return None
        try:
            message = json.loads(line)
        except ValueError as exc:
            logger.debug("malformed JSON-RPC frame: %s", exc)
            return _error(None, PARSE_ERROR, f"Parse error: {exc}")
        return await self.handle_message(message)

    async def handle_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "Invalid Request: expected a JSON object")

        method = message.get("method")
        is_notification = "id" not in message
        msg_id = message.get("id")

        if not isinstance(method, str):
            if is_notification:
                return None
            return _error(msg_id, INVALID_REQUEST, "Invalid Request: missing method")

        # Notifications (notifications/initialized, notifications/cancelled, …)
        # never get a response.
        if is_notification:
            return None

        params = message.get("params")
        if params is None:
            params = {}

        if method == "initialize":
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            return _result(
                msg_id,
                {
                    "protocolVersion": _negotiate_protocol_version(requested),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                },
            )
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": self.tool_definitions()})
        if method == "tools/call":
            return await self.call_tool(params, msg_id)
        if method == "shutdown":
            # Not part of the MCP spec; accepted so a client can ask us to stop.
            return _result(msg_id, {})

        return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    # ── tools ───────────────────────────────────────────────────────────
    def tool_definitions(self) -> list[dict[str, Any]]:
        """MCP tool entries for the allowlist, schema taken from the registry."""
        defs: list[dict[str, Any]] = []
        for name in READ_ONLY_TOOLS:
            tool = self.registry.get(name)
            if tool is None:  # pragma: no cover - only if registration broke
                logger.warning("allowlisted tool %r is not registered", name)
                continue
            defs.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    # The registry's raw schema: tool_defs() adds CrabAgent's
                    # "summary" display parameter, which an MCP client should not see.
                    "inputSchema": copy.deepcopy(tool.parameters),
                }
            )
        return defs

    async def call_tool(self, params: Any, msg_id: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "Invalid params: 'params' must be an object")

        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _error(msg_id, INVALID_PARAMS, "Invalid params: missing tool name")

        # The safety gate: allowlist check happens before any handler lookup or
        # execution, so a mutating or unknown tool can never touch the machine.
        if name not in READ_ONLY_TOOL_SET:
            return _result(msg_id, _tool_result(self.refusal(name), is_error=True))

        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error(msg_id, INVALID_PARAMS, "Invalid params: 'arguments' must be an object")

        escape = self._path_escape(arguments)
        if escape:
            return _result(msg_id, _tool_result(escape, is_error=True))

        result = await self.registry.execute(name, arguments, self.context)
        text = _to_text(result)
        return _result(msg_id, _tool_result(text, is_error=_looks_like_error(text)))

    def refusal(self, name: str) -> str:
        return _REFUSAL.format(name=name, allowed=", ".join(READ_ONLY_TOOLS))

    def _path_escape(self, arguments: dict[str, Any]) -> str | None:
        """Error text when a path argument resolves outside the workspace."""
        for key in _PATH_ARGUMENTS:
            value = arguments.get(key)
            if not isinstance(value, str) or not value:
                continue
            resolved, error = resolve_tool_path(value, self.context)
            if error:
                return error
            if resolved is None:  # pragma: no cover - resolve_tool_path always sets one
                continue
            if not resolved.is_relative_to(self.workspace):
                return (
                    f"Error: path '{value}' resolves outside the workspace {self.workspace}; "
                    "this MCP server only reads inside its workspace."
                )
        return None

    # ── transport ───────────────────────────────────────────────────────
    async def serve(self, stdin: Stream, stdout: Stream) -> None:
        """Read newline-delimited frames until EOF, answering each one."""
        logger.info("MCP server reading frames (workspace=%s)", self.workspace)
        while True:
            line = await asyncio.to_thread(stdin.readline)
            if not line:
                break
            response = await self.handle_line(line)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def run(workspace: Path | str | None = None, *, stdin: Stream | None = None, stdout: Stream | None = None) -> int:
    """Entry point for ``crabagent --mcp-server``; returns the process exit code."""
    server = ReadOnlyMcpServer(workspace or Path.cwd())
    try:
        asyncio.run(server.serve(stdin or sys.stdin, stdout or sys.stdout))
    except KeyboardInterrupt:
        pass
    return 0

"""Read-only MCP server: exposes a small allowlist of CrabAgent tools over stdio.

``crabagent --mcp-server`` speaks the Model Context Protocol on stdin/stdout,
built on the official ``mcp`` SDK (the same dependency CrabAgent's *client*
already uses), so the wire format, handshake and frame parsing are the SDK's
rather than something this module maintains by hand.

Only three things are registered with the SDK: ``tools/list`` and ``tools/call``
handlers plus the standard ``initialize``/``ping`` machinery the SDK owns.

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

Nothing but MCP frames is ever written to stdout; logs go to stderr.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

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


def _text_content(text: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=text)]


def _tool_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    """MCP CallToolResult carrying a single text block."""
    return types.CallToolResult(content=_text_content(text), is_error=is_error)


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


class ReadOnlyMcpServer:
    """MCP server exposing only ``READ_ONLY_TOOLS`` inside one workspace."""

    def __init__(self, workspace: Path | str, tool_registry: ToolRegistry | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        # A private AgentContext keeps the tools inside the workspace. Its empty
        # tool_permissions means every tool stays on its own default (auto).
        self.context = AgentContext(workspace=self.workspace)
        self.registry = tool_registry or registry
        # The SDK's low-level server: it owns initialize/ping/frame parsing, and
        # dispatches only the two handlers below. The allowlist stays ours.
        self.server: Server[Any] = Server(
            SERVER_NAME,
            version=__version__,
            on_list_tools=self.handle_list_tools,
            on_call_tool=self.handle_call_tool,
        )

    # ── handlers registered with the SDK ────────────────────────────────
    async def handle_list_tools(
        self,
        ctx: Any = None,
        params: Any = None,
    ) -> types.ListToolsResult:
        """``tools/list``: exactly the allowlist, schemas from the registry."""
        return types.ListToolsResult(tools=self.tool_definitions())

    async def handle_call_tool(
        self,
        ctx: Any = None,
        params: types.CallToolRequestParams | None = None,
    ) -> types.CallToolResult:
        """``tools/call``: refuse anything outside the allowlist, then run it."""
        name = getattr(params, "name", None)
        if not isinstance(name, str) or not name:
            # The SDK validates params before we get here, so this is defensive.
            return _tool_result("Error: missing tool name.", is_error=True)

        # The safety gate: allowlist check happens before any handler lookup or
        # execution, so a mutating or unknown tool can never touch the machine.
        if name not in READ_ONLY_TOOL_SET:
            return _tool_result(self.refusal(name), is_error=True)

        arguments = getattr(params, "arguments", None) or {}
        if not isinstance(arguments, dict):
            return _tool_result("Error: 'arguments' must be an object.", is_error=True)

        escape = self._path_escape(arguments)
        if escape:
            return _tool_result(escape, is_error=True)

        result = await self.registry.execute(name, arguments, self.context)
        text = _to_text(result)
        return _tool_result(text, is_error=_looks_like_error(text))

    # ── tools ───────────────────────────────────────────────────────────
    def tool_definitions(self) -> list[types.Tool]:
        """MCP tool entries for the allowlist, schema taken from the registry."""
        defs: list[types.Tool] = []
        for name in READ_ONLY_TOOLS:
            tool = self.registry.get(name)
            if tool is None:  # pragma: no cover - only if registration broke
                logger.warning("allowlisted tool %r is not registered", name)
                continue
            defs.append(
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    # The registry's raw schema: tool_defs() adds CrabAgent's
                    # "summary" display parameter, which an MCP client should not see.
                    input_schema=json.loads(json.dumps(tool.parameters)),
                )
            )
        return defs

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
    def create_initialization_options(self) -> Any:
        """Handshake options (server name/version) the SDK advertises."""
        return self.server.create_initialization_options()

    async def serve_stdio(self) -> None:
        """Serve one stdio session until the client closes stdin."""
        logger.info("MCP server reading frames (workspace=%s)", self.workspace)
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream, self.create_initialization_options())


def run_mcp_server(workspace: Path | str | None = None) -> int:
    """Entry point for ``crabagent --mcp-server``; returns the process exit code."""
    server = ReadOnlyMcpServer(workspace or Path.cwd())
    try:
        anyio.run(server.serve_stdio)
    except KeyboardInterrupt:  # pragma: no cover - interactive Ctrl-C
        pass
    return 0


# Backwards-compatible alias: ``cli/__main__.py`` imports ``run``.
run = run_mcp_server


def _main() -> int:  # pragma: no cover - thin CLI shim
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    return run_mcp_server()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

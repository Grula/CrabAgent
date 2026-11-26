"""Read-only MCP server: protocol shape, allowlist and refusal behaviour.

Most tests drive the real `mcp` SDK in-process (a ``ClientSession`` connected to
the low-level ``Server`` over memory streams), so handshake, ``tools/list`` and
``tools/call`` are exercised through the same protocol objects a client uses.
Transport-level behaviour (malformed frames, unknown methods, version
negotiation) is checked against a real ``crabagent --mcp-server`` process, and
one end-to-end test drives that process with a real ``ClientSession``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, types
from mcp.shared.memory import create_client_server_memory_streams

from crabagent.mcp_server import READ_ONLY_TOOLS, ReadOnlyMcpServer

REPO_ROOT = Path(__file__).resolve().parents[1]
MUTATING_TOOLS = ["write", "edit", "bash", "run_tests", "update_agents_md"]
LATEST_HANDSHAKE_VERSION = "2025-11-25"


@asynccontextmanager
async def _session(workspace: Path):
    """A connected SDK client session against our server, in-process."""
    mcp_server = ReadOnlyMcpServer(workspace)
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                mcp_server.server.run,
                server_streams[0],
                server_streams[1],
                mcp_server.create_initialization_options(),
            )
            try:
                async with ClientSession(client_streams[0], client_streams[1]) as session:
                    await session.initialize()
                    yield session
            finally:
                tg.cancel_scope.cancel()


def _raw_process(workspace: Path, *frames: str) -> list[dict]:
    """Feed raw newline-delimited frames to a real server process.

    A trailing ``ping`` sentinel is appended: the SDK may cancel the request
    still in flight when stdin hits EOF, and the sentinel absorbs that, keeping
    the frames under test answered deterministically.
    """
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))
    all_frames = (*frames, _request(999, "ping"))
    completed = subprocess.run(
        [sys.executable, "-m", "crabagent", "--mcp-server", "--workspace", str(workspace)],
        input="".join((frame if isinstance(frame, str) else json.dumps(frame)) + "\n" for frame in all_frames),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return [json.loads(line) for line in completed.stdout.splitlines() if line]


def _reply(responses: list[dict], msg_id: int) -> dict:
    """The response frame for ``msg_id`` (frames may arrive in any order)."""
    for response in responses:
        if response.get("id") == msg_id:
            return response
    raise AssertionError(f"no reply for id {msg_id}: {responses!r}")


def _request(msg_id: int, method: str, params: dict | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _initialize(protocol_version: str, msg_id: int = 1) -> str:
    return json.dumps(
        _request(
            msg_id,
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
    )


INITIALIZED = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})


# ── handshake and listing ───────────────────────────────────────────────
async def test_initialize_handshake_shape(tmp_path):
    async with _session(tmp_path) as session:
        init = session.initialize_result
        negotiated = session.protocol_version

    assert init is not None
    assert init.protocol_version == LATEST_HANDSHAKE_VERSION
    assert init.capabilities.tools is not None
    assert init.server_info.name == "crabagent"
    assert isinstance(init.server_info.version, str) and init.server_info.version
    # notifications/initialized was accepted by the SDK runner during initialize().
    assert negotiated == LATEST_HANDSHAKE_VERSION


def test_initialize_echoes_supported_client_protocol_version(tmp_path):
    responses = _raw_process(tmp_path, _initialize("2025-06-18"), INITIALIZED)

    response = _reply(responses, 1)
    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert response["result"]["serverInfo"]["name"] == "crabagent"


def test_initialize_counter_offers_latest_for_unknown_version(tmp_path):
    # The SDK owns negotiation: an unsupported offer gets the newest handshake
    # revision instead of an error (the hand-written server did the same).
    responses = _raw_process(tmp_path, _initialize("1999-01-01"))

    assert _reply(responses, 1)["result"]["protocolVersion"] == LATEST_HANDSHAKE_VERSION


async def test_ping_returns_empty_result(tmp_path):
    async with _session(tmp_path) as session:
        pong = await session.send_ping()

    assert isinstance(pong, types.EmptyResult)


async def test_tools_list_is_exactly_the_read_only_allowlist(tmp_path):
    async with _session(tmp_path) as session:
        tools = (await session.list_tools()).tools

    names = [t.name for t in tools]
    assert names == list(READ_ONLY_TOOLS) == ["read", "glob", "grep"]
    for mutating in MUTATING_TOOLS:
        assert mutating not in names
    for tool in tools:
        assert tool.description
        assert tool.input_schema["type"] == "object"
        assert "summary" not in tool.input_schema["properties"]

    read_schema = tools[0].input_schema
    # file_path or files (a batch): neither is required on its own.
    assert {"file_path", "files"} <= set(read_schema["properties"])


# ── allowlisted calls actually work ─────────────────────────────────────
async def test_tools_call_read_returns_file_text(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbravo\n", encoding="utf-8")

    async with _session(tmp_path) as session:
        result = await session.call_tool("read", {"file_path": "notes.txt"})

    assert result.is_error is False
    assert [block.text for block in result.content] == [
        "[File: notes.txt (2 lines total)]\n1: alpha\n2: bravo"
    ]


async def test_tools_call_glob_and_grep_run(tmp_path):
    (tmp_path / "a.py").write_text("print('alpha')\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("alpha\n", encoding="utf-8")

    async with _session(tmp_path) as session:
        globbed = await session.call_tool("glob", {"pattern": "*.py"})
        grepped = await session.call_tool("grep", {"pattern": "alpha", "include": "*.txt"})

    assert globbed.content[0].text.strip() == "a.py"
    assert "b.txt:1: alpha" in grepped.content[0].text


async def test_tool_error_is_reported_as_is_error(tmp_path):
    # A missing file is an allowlisted tool's own error, not a protocol error.
    async with _session(tmp_path) as session:
        result = await session.call_tool("read", {"file_path": "missing.txt"})

    assert result.is_error is True
    assert "path does not exist" in result.content[0].text


# ── refusal path ────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", MUTATING_TOOLS)
async def test_mutating_tool_is_refused_and_writes_nothing(tmp_path, name):
    target = tmp_path / "target.txt"
    (tmp_path / "existing.txt").write_text("original\n", encoding="utf-8")

    arguments = {
        "write": {"file_path": str(target), "content": "pwned"},
        "edit": {"file_path": str(tmp_path / "existing.txt"), "old_string": "original", "new_string": "pwned"},
        "bash": {"command": f"touch {target}"},
        "run_tests": {"target": "tests/test_mcp_server.py"},
        "update_agents_md": {"content": "pwned"},
    }[name]

    async with _session(tmp_path) as session:
        result = await session.call_tool(name, arguments)

    assert result.is_error is True
    text = result.content[0].text
    assert name in text and "read-only" in text
    assert not target.exists()
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "original\n"


async def test_write_call_leaves_no_files_at_all(tmp_path):
    async with _session(tmp_path) as session:
        result = await session.call_tool("write", {"file_path": "new.txt", "content": "pwned"})

    assert result.is_error is True
    assert sorted(p.name for p in tmp_path.iterdir()) == []


async def test_unknown_tool_is_refused_with_the_same_message(tmp_path):
    async with _session(tmp_path) as session:
        result = await session.call_tool("definitely_not_a_tool", {})

    assert result.is_error is True
    text = result.content[0].text
    assert "definitely_not_a_tool" in text and "read-only" in text


async def test_refusal_list_matches_allowlist_everywhere(tmp_path):
    server = ReadOnlyMcpServer(tmp_path)
    for name in ("write", "edit", "bash", "nope"):
        assert server.refusal(name) == (
            f"Error: refusing to run tool '{name}': this MCP server is read-only "
            "and exposes only: read, glob, grep."
        )


# ── workspace containment ───────────────────────────────────────────────
async def test_path_escape_is_refused(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret\n", encoding="utf-8")

    async with _session(workspace) as session:
        results = [
            await session.call_tool("read", {"file_path": "../secret.txt"}),
            await session.call_tool("read", {"file_path": str(secret)}),
            await session.call_tool("glob", {"pattern": "*.txt", "path": ".."}),
        ]

    for result in results:
        assert result.is_error is True
        assert "outside the workspace" in result.content[0].text
        assert "top secret" not in result.content[0].text


async def test_symlink_escape_is_refused(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret\n", encoding="utf-8")
    (workspace / "link.txt").symlink_to(secret)

    async with _session(workspace) as session:
        result = await session.call_tool("read", {"file_path": "link.txt"})

    assert result.is_error is True
    assert "outside the workspace" in result.content[0].text


async def test_grep_does_not_follow_symlinks_outside_the_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret-token\n", encoding="utf-8")
    (workspace / "notes.txt").symlink_to(outside / "secret.txt")
    inner = workspace / "inner.txt"
    inner.write_text("inside-secret-token\n", encoding="utf-8")
    (workspace / "alias.txt").symlink_to(inner)

    async with _session(workspace) as session:
        result = await session.call_tool("grep", {"pattern": "secret-token"})

    assert result.is_error is False
    text = result.content[0].text
    assert "outside-secret-token" not in text
    # a link that stays inside the workspace is still searched
    assert "inside-secret-token" in text


# ── protocol robustness (SDK-owned transport) ───────────────────────────
def test_malformed_json_does_not_kill_the_session(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")

    responses = _raw_process(
        tmp_path,
        _initialize("2025-06-18"),
        INITIALIZED,
        "this is not json",
        _request(2, "tools/call", {"name": "read", "arguments": {"file_path": "notes.txt"}}),
    )

    # The SDK's reader owns framing: an undecodable frame is never answered with
    # -32700 (the hand-written loop replied with a parse error). The process must
    # still exit cleanly with JSON-RPC-only stdout (checked in _raw_process), the
    # handshake must work, and the follow-up call must either be served or be
    # reported as a closed connection -- never crash or corrupt the stream.
    assert all(isinstance(r.get("id"), int) for r in responses)
    assert _reply(responses, 1)["result"]["serverInfo"]["name"] == "crabagent"
    assert all(response.get("error", {}).get("code") != -32700 for response in responses)
    if any(response.get("id") == 2 for response in responses):
        served = _reply(responses, 2)
        if "result" in served:
            assert "1: alpha" in served["result"]["content"][0]["text"]
        else:
            assert served["error"]["message"] == "Connection closed"


def test_unknown_method_returns_method_not_found(tmp_path):
    responses = _raw_process(tmp_path, _initialize("2025-06-18"), INITIALIZED, _request(9, "resources/list"))

    error = _reply(responses, 9)["error"]
    assert error["code"] == -32601
    assert "Method not found" in error["message"]


def test_tools_call_without_name_is_invalid_params(tmp_path):
    responses = _raw_process(
        tmp_path,
        _initialize("2025-06-18"),
        INITIALIZED,
        json.dumps(_request(5, "tools/call", {"arguments": {}})),
    )

    assert _reply(responses, 5)["error"]["code"] == -32602


# ── the real process ────────────────────────────────────────────────────
def test_real_process_session_over_stdio(tmp_path):
    """A real SDK client against a real ``crabagent --mcp-server`` process."""

    async def scenario() -> None:
        (tmp_path / "notes.txt").write_text("hello from the workspace\n", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))

        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "crabagent", "--mcp-server", "--workspace", str(tmp_path)],
            env=env,
            cwd=str(REPO_ROOT),
        )
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                init = await client.initialize()
                assert init.server_info.name == "crabagent"

                tools = await client.list_tools()
                assert [t.name for t in tools.tools] == ["read", "glob", "grep"]

                read_result = await client.call_tool("read", {"file_path": "notes.txt"})
                assert "hello from the workspace" in read_result.content[0].text

                write_result = await client.call_tool("write", {"file_path": "pwned.txt", "content": "pwned"})
                assert write_result.is_error is True
                assert "read-only" in write_result.content[0].text

        assert not (tmp_path / "pwned.txt").exists()

    anyio.run(scenario)

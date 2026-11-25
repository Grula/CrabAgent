"""Read-only MCP server: protocol shape, allowlist and refusal behaviour.

Most tests drive the newline-delimited transport over in-memory streams; one
spawns the real ``crabagent --mcp-server`` process.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from crabagent.mcp_server import READ_ONLY_TOOLS, ReadOnlyMcpServer

REPO_ROOT = Path(__file__).resolve().parents[1]
MUTATING_TOOLS = ["write", "edit", "bash", "run_tests", "update_agents_md"]


def _frame(message) -> str:
    return message if isinstance(message, str) else json.dumps(message)


def _frames(*messages) -> str:
    return "".join(_frame(m) + "\n" for m in messages)


async def _drive(workspace: Path, *messages) -> list[dict]:
    """Run one session over in-memory streams and return the decoded responses."""
    server = ReadOnlyMcpServer(workspace)
    out = io.StringIO()
    await server.serve(io.StringIO(_frames(*messages)), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _request(msg_id: int, method: str, params: dict | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _call(msg_id: int, name: str, arguments: dict) -> dict:
    return _request(msg_id, "tools/call", {"name": name, "arguments": arguments})


INITIALIZE = _request(
    1,
    "initialize",
    {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
)
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


# ── handshake and listing ───────────────────────────────────────────────
async def test_initialize_handshake_shape(tmp_path):
    responses = await _drive(tmp_path, INITIALIZE, INITIALIZED)

    # notifications/initialized produces no response at all.
    assert len(responses) == 1
    response = responses[0]
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "crabagent"
    assert isinstance(result["serverInfo"]["version"], str) and result["serverInfo"]["version"]


async def test_initialize_without_protocol_version_reports_default(tmp_path):
    responses = await _drive(tmp_path, _request(1, "initialize", {}))

    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"


async def test_ping_returns_empty_result(tmp_path):
    responses = await _drive(tmp_path, _request(1, "ping"))

    assert responses[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}


async def test_tools_list_is_exactly_the_read_only_allowlist(tmp_path):
    responses = await _drive(tmp_path, INITIALIZE, INITIALIZED, _request(2, "tools/list"))

    tools = responses[1]["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == list(READ_ONLY_TOOLS) == ["read", "glob", "grep"]
    for mutating in MUTATING_TOOLS:
        assert mutating not in names
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"
        assert "summary" not in tool["inputSchema"]["properties"]

    read_schema = tools[0]["inputSchema"]
    assert read_schema["required"] == ["file_path"]


# ── allowlisted calls actually work ─────────────────────────────────────
async def test_tools_call_read_returns_file_text(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbravo\n", encoding="utf-8")

    responses = await _drive(tmp_path, _call(3, "read", {"file_path": "notes.txt"}))

    result = responses[0]["result"]
    assert result["isError"] is False
    assert result["content"] == [
        {"type": "text", "text": "[File: notes.txt (2 lines total)]\n1: alpha\n2: bravo"}
    ]


async def test_tools_call_glob_and_grep_run(tmp_path):
    (tmp_path / "a.py").write_text("print('alpha')\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("alpha\n", encoding="utf-8")

    responses = await _drive(
        tmp_path,
        _call(1, "glob", {"pattern": "*.py"}),
        _call(2, "grep", {"pattern": "alpha", "include": "*.txt"}),
    )

    assert responses[0]["result"]["content"][0]["text"].strip() == "a.py"
    assert "b.txt:1: alpha" in responses[1]["result"]["content"][0]["text"]


async def test_tool_error_is_reported_as_is_error(tmp_path):
    # A missing file is an allowlisted tool's own error, not a protocol error.
    responses = await _drive(tmp_path, _call(1, "read", {"file_path": "missing.txt"}))

    result = responses[0]["result"]
    assert result["isError"] is True
    assert "path does not exist" in result["content"][0]["text"]


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

    responses = await _drive(tmp_path, _call(1, name, arguments))

    result = responses[0]["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert name in text and "read-only" in text
    assert not target.exists()
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "original\n"


async def test_write_call_leaves_no_files_at_all(tmp_path):
    responses = await _drive(
        tmp_path,
        _call(1, "write", {"file_path": "new.txt", "content": "pwned"}),
    )

    assert responses[0]["result"]["isError"] is True
    assert sorted(p.name for p in tmp_path.iterdir()) == []


async def test_unknown_tool_is_refused_with_the_same_message(tmp_path):
    responses = await _drive(tmp_path, _call(1, "definitely_not_a_tool", {}))

    result = responses[0]["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
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

    responses = await _drive(
        workspace,
        _call(1, "read", {"file_path": "../secret.txt"}),
        _call(2, "read", {"file_path": str(secret)}),
        _call(3, "glob", {"pattern": "*.txt", "path": ".."}),
    )

    for response in responses:
        result = response["result"]
        assert result["isError"] is True
        assert "outside the workspace" in result["content"][0]["text"]
        assert "top secret" not in result["content"][0]["text"]


async def test_symlink_escape_is_refused(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret\n", encoding="utf-8")
    (workspace / "link.txt").symlink_to(secret)

    responses = await _drive(workspace, _call(1, "read", {"file_path": "link.txt"}))

    assert responses[0]["result"]["isError"] is True
    assert "outside the workspace" in responses[0]["result"]["content"][0]["text"]


# ── protocol robustness ─────────────────────────────────────────────────
async def test_malformed_json_does_not_kill_the_loop(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")

    responses = await _drive(
        tmp_path,
        "this is not json",
        "",
        _call(2, "read", {"file_path": "notes.txt"}),
    )

    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None
    # The loop survived: the next frame was served normally.
    assert len(responses) == 2
    assert "1: alpha" in responses[1]["result"]["content"][0]["text"]


async def test_unknown_method_returns_method_not_found(tmp_path):
    responses = await _drive(tmp_path, _request(9, "resources/list"))

    assert responses[0]["error"]["code"] == -32601
    assert "resources/list" in responses[0]["error"]["message"]
    assert responses[0]["id"] == 9


async def test_tools_call_without_name_is_invalid_params(tmp_path):
    responses = await _drive(tmp_path, _request(1, "tools/call", {"arguments": {}}))

    assert responses[0]["error"]["code"] == -32602


# ── the real process ────────────────────────────────────────────────────
def test_real_process_session_over_stdio(tmp_path):
    (tmp_path / "notes.txt").write_text("hello from the workspace\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))
    frames = _frames(
        INITIALIZE,
        INITIALIZED,
        _request(2, "tools/list"),
        _call(3, "read", {"file_path": "notes.txt"}),
        _call(4, "write", {"file_path": "pwned.txt", "content": "pwned"}),
    )

    completed = subprocess.run(
        [sys.executable, "-m", "crabagent", "--mcp-server", "--workspace", str(tmp_path)],
        input=frames,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0
    lines = [line for line in completed.stdout.splitlines() if line]
    responses = [json.loads(line) for line in lines]  # stdout is JSON-RPC frames only
    assert [r["id"] for r in responses] == [1, 2, 3, 4]
    assert responses[0]["result"]["serverInfo"]["name"] == "crabagent"
    assert [t["name"] for t in responses[1]["result"]["tools"]] == ["read", "glob", "grep"]
    assert "hello from the workspace" in responses[2]["result"]["content"][0]["text"]
    assert responses[3]["result"]["isError"] is True
    assert "read-only" in responses[3]["result"]["content"][0]["text"]
    assert not (tmp_path / "pwned.txt").exists()

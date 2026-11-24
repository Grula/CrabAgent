"""Tests for the /files API: session scoping, read hardening, write safety."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from crabagent.serve.api.files import _is_relative_to


def test_is_relative_to_detects_nested_path(tmp_path: Path):
    parent = tmp_path / "folder"
    child = parent / "nested"
    child.mkdir(parents=True)

    assert _is_relative_to(child, parent)
    assert not _is_relative_to(parent, child)


def test_relative_path_resolution_blocks_workspace_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from crabagent.serve.api import files
    from crabagent.core.config import settings

    monkeypatch.setattr(settings, "workspace", tmp_path)
    with pytest.raises(HTTPException, match="Path outside workspace"):
        files._resolve_file_path("../outside")


# ── helpers ──────────────────────────────────────────────────────────


def _user(user_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(id=user_id)


def _fake_request(session_id: str | None) -> tuple[SimpleNamespace, asyncio.Queue]:
    """Minimal Request stand-in exposing app.state.event_queues.

    Registers the production 4-tuple ``(sid, critical_queue, stream_queue, ts)``
    so an event put on both queues shows up as a duplicate; the stream queue is
    returned because non-critical events must land there only.
    """
    critical: asyncio.Queue = asyncio.Queue()
    stream: asyncio.Queue = asyncio.Queue()
    state = SimpleNamespace(event_queues={"q": (session_id, critical, stream, time.time())})
    return SimpleNamespace(app=SimpleNamespace(state=state)), stream


def _drain(*queues: asyncio.Queue) -> list:
    events = []
    for queue in queues:
        while not queue.empty():
            events.append(queue.get_nowait())
    return events


def _patch_conversation(monkeypatch: pytest.MonkeyPatch, conv: SimpleNamespace | None) -> None:
    from crabagent.serve.services import conversation as conv_svc

    async def _get_conversation(db, session_id):
        return conv

    monkeypatch.setattr(conv_svc, "get_conversation", _get_conversation)


@pytest.fixture
def backends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Server CWD workspace and a separate chat workspace."""
    from crabagent.core.config import settings

    server_ws = tmp_path / "server-workspace"
    chat_ws = tmp_path / "chat-workspace"
    server_ws.mkdir()
    chat_ws.mkdir()
    monkeypatch.setattr(settings, "workspace", server_ws)
    return SimpleNamespace(server=server_ws, chat=chat_ws, tmp=tmp_path)


@pytest.fixture
async def molt_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the app's async_session_factory at a throwaway SQLite file."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import crabagent.core.database as database

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'molts.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    yield factory
    await engine.dispose()


# ── 1. session scoping ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tree_read_write_use_the_chat_workspace(backends, monkeypatch):
    from crabagent.serve.api import files

    (backends.server / "server_only.py").write_text("server\n", encoding="utf-8")
    (backends.chat / "chat_only.py").write_text("print(1)\n", encoding="utf-8")
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=str(backends.chat)))
    user = _user()

    tree = await files.list_tree(
        path="", depth=1, absolute=False, session_id="sess-1", user=user, db=None
    )
    assert [entry["name"] for entry in tree] == ["chat_only.py"]

    read = await files.read_file(
        path="chat_only.py",
        absolute=False,
        start_line=None,
        end_line=None,
        session_id="sess-1",
        user=user,
        db=None,
    )
    assert read["content"] == "print(1)\n"
    assert read["size"] == len("print(1)\n")
    assert read["mtime_ms"] > 0

    request, _queue = _fake_request("sess-1")
    written = await files.write_file(
        files.WriteFileRequest(path="new.py", content="x = 1\n", session_id="sess-1"),
        request,
        user=user,
        db=None,
    )
    assert written["status"] == "ok"
    assert (backends.chat / "new.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (backends.server / "new.py").exists()
    assert written["size"] == len("x = 1\n")
    assert written["mtime_ms"] > 0


@pytest.mark.asyncio
async def test_unknown_session_falls_back_to_server_workspace(backends, monkeypatch):
    from crabagent.serve.api import files

    (backends.server / "server_only.py").write_text("server\n", encoding="utf-8")
    (backends.chat / "chat_only.py").write_text("chat\n", encoding="utf-8")
    _patch_conversation(monkeypatch, None)
    user = _user()

    tree = await files.list_tree(
        path="", depth=1, absolute=False, session_id="nope", user=user, db=None
    )
    assert [entry["name"] for entry in tree] == ["server_only.py"]

    no_session = await files.list_tree(
        path="", depth=1, absolute=False, session_id=None, user=user, db=None
    )
    assert [entry["name"] for entry in no_session] == ["server_only.py"]


@pytest.mark.asyncio
async def test_foreign_session_does_not_leak_its_workspace(backends, monkeypatch):
    from crabagent.serve.api import files

    (backends.chat / "chat_only.py").write_text("chat\n", encoding="utf-8")
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=999, workspace=str(backends.chat)))

    with pytest.raises(HTTPException, match="File not found"):
        await files.read_file(
            path="chat_only.py",
            absolute=False,
            start_line=None,
            end_line=None,
            session_id="someone-elses",
            user=_user(),
            db=None,
        )


# ── 2. read hardening ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_binary_file_returns_binary_without_content(backends, monkeypatch):
    from crabagent.serve.api import files

    (backends.chat / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=str(backends.chat)))

    result = await files.read_file(
        path="blob.bin",
        absolute=False,
        start_line=None,
        end_line=None,
        session_id="sess-1",
        user=_user(),
        db=None,
    )

    assert result["binary"] is True
    assert result["content"] == ""
    assert result["truncated"] is False
    assert result["size"] == len(b"\x00\x01\x02binary")


@pytest.mark.asyncio
async def test_read_pages_with_start_and_end_line(backends, monkeypatch):
    from crabagent.serve.api import files

    (backends.chat / "big.txt").write_text(
        "".join(f"line {i}\n" for i in range(1, 6)), encoding="utf-8"
    )
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=str(backends.chat)))

    page = await files.read_file(
        path="big.txt",
        absolute=False,
        start_line=2,
        end_line=3,
        session_id="sess-1",
        user=_user(),
        db=None,
    )

    assert page["content"] == "line 2\nline 3\n"
    assert page["start_line"] == 2
    assert page["end_line"] == 3
    assert page["has_more"] is True
    assert page["binary"] is False

    last_page = await files.read_file(
        path="big.txt",
        absolute=False,
        start_line=5,
        end_line=99,
        session_id="sess-1",
        user=_user(),
        db=None,
    )
    assert last_page["content"] == "line 5\n"
    assert last_page["end_line"] == 5
    assert last_page["has_more"] is False


@pytest.mark.asyncio
async def test_paged_read_starting_past_eof_does_not_invert_the_range(backends, monkeypatch):
    from crabagent.serve.api import files

    (backends.chat / "short.txt").write_text("one\ntwo\n", encoding="utf-8")
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=str(backends.chat)))

    page = await files.read_file(
        path="short.txt",
        absolute=False,
        start_line=50,
        end_line=None,
        session_id="sess-1",
        user=_user(),
        db=None,
    )

    assert page["content"] == ""
    assert page["start_line"] == 50
    # An empty chunk must not report end_line < start_line.
    assert page["end_line"] >= page["start_line"]
    assert page["end_line"] == 50
    assert page["has_more"] is False


@pytest.mark.asyncio
async def test_non_utf8_read_round_trips_through_write(backends, monkeypatch):
    from crabagent.serve.api import files

    raw = b"caf\xe9\n"
    (backends.chat / "latin.txt").write_bytes(raw)
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=str(backends.chat)))
    user = _user()

    read = await files.read_file(
        path="latin.txt",
        absolute=False,
        start_line=None,
        end_line=None,
        session_id="sess-1",
        user=user,
        db=None,
    )
    assert read["binary"] is False
    assert read["encoding"] == "latin-1"
    assert read["content"] == "caf\xe9\n"

    request, _queue = _fake_request("sess-1")
    await files.write_file(
        files.WriteFileRequest(
            path="latin.txt", content=read["content"], session_id="sess-1", encoding="latin-1"
        ),
        request,
        user=user,
        db=None,
    )
    assert (backends.chat / "latin.txt").read_bytes() == raw


# ── 3. write safety ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_rejects_stale_expected_mtime_with_409(backends, monkeypatch):
    from crabagent.serve.api import files

    target = backends.chat / "edit_me.py"
    target.write_text("v = 1\n", encoding="utf-8")
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=str(backends.chat)))
    user = _user()
    request, _queue = _fake_request("sess-1")

    stale = int(target.stat().st_mtime * 1000) - 5000
    with pytest.raises(HTTPException) as exc:
        await files.write_file(
            files.WriteFileRequest(
                path="edit_me.py", content="v = 2\n", session_id="sess-1", expected_mtime_ms=stale
            ),
            request,
            user=user,
            db=None,
        )
    assert exc.value.status_code == 409
    assert "changed on disk" in str(exc.value.detail)
    assert target.read_text(encoding="utf-8") == "v = 1\n"

    current = int(target.stat().st_mtime * 1000)
    ok = await files.write_file(
        files.WriteFileRequest(
            path="edit_me.py", content="v = 2\n", session_id="sess-1", expected_mtime_ms=current
        ),
        request,
        user=user,
        db=None,
    )
    assert ok["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "v = 2\n"


@pytest.mark.asyncio
async def test_ui_write_creates_molt_row_and_rollback_restores(backends, molt_factory, monkeypatch):
    from crabagent.core.molt.rollback import rollback
    from crabagent.core.molt.store import list_molts
    from crabagent.serve.api import files

    target = backends.chat / "app.py"
    target.write_text("before\n", encoding="utf-8")
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=str(backends.chat)))
    request, _queue = _fake_request("sess-1")

    await files.write_file(
        files.WriteFileRequest(path="app.py", content="after\n", session_id="sess-1"),
        request,
        user=_user(),
        db=None,
    )
    assert target.read_text(encoding="utf-8") == "after\n"

    async with molt_factory() as db:
        molts = await list_molts(db, "sess-1")
    assert len(molts) == 1
    assert molts[0]["file_count"] == 1

    restored = await rollback(molts[0]["molt_id"], backends.chat)
    assert restored == ["app.py"]
    assert target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.asyncio
async def test_ui_write_emits_one_file_updated_event(backends, monkeypatch):
    from crabagent.core.event import EventType
    from crabagent.serve.api import files

    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=str(backends.chat)))
    request, stream = _fake_request("sess-1")
    critical = request.app.state.event_queues["q"][1]

    await files.write_file(
        files.WriteFileRequest(path="f.py", content="a = 1\n", session_id="sess-1"),
        request,
        user=_user(),
        db=None,
    )

    # Both queues are drained: FILE_UPDATED is not critical, so it must be queued
    # on the stream queue only and delivered to the client exactly once.
    events = _drain(critical, stream)
    updates = [e for e in events if e.type == EventType.FILE_UPDATED]
    assert len(updates) == 1
    assert critical.empty()
    assert stream.empty()
    assert updates[0].data["path"] == "f.py"
    assert updates[0].data["origin"] == "ui"
    assert updates[0].data["mtime_ms"] > 0


# ── 4. FILE_UPDATED from agent tools ─────────────────────────────────


@pytest.mark.asyncio
async def test_agent_edit_emits_exactly_one_file_updated(tmp_path: Path):
    from crabagent.core.agent.context import AgentContext
    from crabagent.core.agent.tools.registry import registry
    from crabagent.core.event import EventBus, EventType

    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    target = workspace / "pkg" / "mod.py"
    target.write_text("a\n", encoding="utf-8")

    events = []

    async def collector(event):
        events.append(event)

    bus = EventBus(name="test")
    bus.subscribe(collector)
    context = AgentContext(workspace=workspace, event_bus=bus)
    context.metadata["_batch_molt"] = True  # snapshots batched by the loop; not under test

    def fake_edit(file_path: str, old_string: str = "", new_string: str = "", context=None) -> str:
        path = Path(file_path)
        text = path.read_text(encoding="utf-8")
        if old_string not in text:
            return f"Error: old_string not found in {file_path}"
        path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"Successfully replaced 1 occurrence in {file_path}"

    tool_registry = registry.clone()
    tool_registry.register(
        name="edit",
        description="fake edit",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["file_path"],
        },
    )(fake_edit)

    result = await tool_registry.execute(
        "edit", {"file_path": str(target), "old_string": "a", "new_string": "b"}, context
    )
    assert str(result).startswith("Successfully")

    updates = [e for e in events if e.type == EventType.FILE_UPDATED]
    assert len(updates) == 1
    assert updates[0].data["path"] == "pkg/mod.py"
    assert updates[0].data["abs_path"] == str(target)
    assert updates[0].data["tool"] == "edit"
    assert updates[0].data["origin"] == "agent"

    # A failed edit must stay silent.
    await tool_registry.execute(
        "edit", {"file_path": str(target), "old_string": "zzz", "new_string": "y"}, context
    )
    assert len([e for e in events if e.type == EventType.FILE_UPDATED]) == 1


# ── 5. active-file prompt context ────────────────────────────────────


def test_file_context_block_mentions_path_and_language(tmp_path: Path):
    from crabagent.serve.api.files import build_file_context_block

    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    block = build_file_context_block("main.py", tmp_path, workspace_type="code")

    assert "`main.py`" in block
    assert "Language: python" in block
    assert "open in the editor" in block
    assert "Unsaved editor buffer" not in block
    assert "print(1)" not in block  # clean buffer: no content sniffing of the disk copy


def test_file_context_block_includes_dirty_buffer(tmp_path: Path):
    from crabagent.serve.api.files import build_file_context_block

    block = build_file_context_block(
        "main.py", tmp_path, dirty=True, content="print('unsaved')\n"
    )

    assert "Unsaved editor buffer" in block
    assert "STALE" in block
    assert "print('unsaved')" in block


def test_file_context_block_is_path_only_for_binary(tmp_path: Path):
    from crabagent.serve.api.files import build_file_context_block

    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x01")
    block = build_file_context_block("logo.png", tmp_path)

    assert "`logo.png`" in block
    assert "binary" in block.lower()
    assert "\x00" not in block

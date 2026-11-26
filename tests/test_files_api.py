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
    return SimpleNamespace(id=user_id, enabled=True)


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
    """Server CWD workspace, a separate chat workspace and the simulated home.

    ``tmp_path/home`` plays ``Path.home()``: the /files routes only serve paths
    under home (or the server workspace), and in production a chat workspace is
    an ordinary folder under the user's home.
    """
    from crabagent.core.config import settings

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    server_ws = home / "server-workspace"
    chat_ws = home / "chat-workspace"
    server_ws.mkdir()
    chat_ws.mkdir()
    monkeypatch.setattr(settings, "workspace", server_ws)
    return SimpleNamespace(server=server_ws, chat=chat_ws, home=home, tmp=tmp_path)


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


# ── 3b. path confinement (audit batch 1, items 2-5) ──────────────────


@pytest.mark.asyncio
async def test_absolute_read_outside_home_is_forbidden(backends, monkeypatch):
    from crabagent.serve.api import files

    home = backends.home
    outside = backends.tmp / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret\n", encoding="utf-8")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("PRIVATE KEY\n", encoding="utf-8")
    (home / ".crabagent").mkdir()
    (home / ".crabagent" / "encryption_key").write_text("k3y\n", encoding="utf-8")
    user = _user()

    for path in (
        str(outside / "secret.txt"),
        str(home / ".ssh" / "id_rsa"),
        str(home / ".crabagent" / "encryption_key"),
        "/etc/passwd",
    ):
        with pytest.raises(HTTPException) as exc:
            await files.read_file(
                path=path,
                absolute=True,
                start_line=None,
                end_line=None,
                session_id=None,
                user=user,
                db=None,
            )
        assert exc.value.status_code == 403, path


@pytest.mark.asyncio
async def test_absolute_tree_and_search_outside_home_are_forbidden(backends, monkeypatch):
    from crabagent.serve.api import files

    home = backends.home
    (home / ".ssh").mkdir()
    user = _user()

    with pytest.raises(HTTPException) as exc:
        await files.list_tree(path="/etc", depth=1, absolute=True, session_id=None, user=user, db=None)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await files.list_tree(
            path=str(home / ".ssh"), depth=1, absolute=True, session_id=None, user=user, db=None
        )
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await files.search_files(q="passwd", path="/etc", absolute=True, limit=10, user=user, db=None)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_relative_search_of_a_denied_directory_is_forbidden(backends, monkeypatch):
    """A $HOME workspace must not expose .crabagent through the relative branch."""
    from crabagent.core.config import settings
    from crabagent.serve.api import files

    monkeypatch.setattr(settings, "workspace", backends.home)
    (backends.home / ".crabagent").mkdir()
    (backends.home / ".crabagent" / "secret.db").write_text("x", encoding="utf-8")
    (backends.home / "notes.txt").write_text("hello\n", encoding="utf-8")
    user = _user()

    with pytest.raises(HTTPException) as exc:
        await files.search_files(
            q="secret", path=".crabagent", absolute=False, limit=10, user=user, db=None
        )
    assert exc.value.status_code == 403

    # A legitimate relative search inside the workspace still works.
    result = await files.search_files(
        q="notes", path="", absolute=False, limit=10, user=user, db=None
    )
    assert [entry["name"] for entry in result["results"]] == ["notes.txt"]


@pytest.mark.asyncio
async def test_absolute_tree_without_path_lists_home(backends, monkeypatch):
    """The workspace picker starts at home: no path means home, not the filesystem root."""
    from crabagent.serve.api import files

    home = backends.home
    (home / "projects").mkdir()
    (home / "notes.md").write_text("home\n", encoding="utf-8")
    user = _user()

    tree = await files.list_tree(
        path="", depth=1, absolute=True, include_root=False, session_id=None, user=user, db=None
    )
    expected = sorted(child.name for child in home.iterdir() if not child.name.startswith("."))
    assert [entry["name"] for entry in tree] == expected
    assert all(entry["path"].startswith(str(home) + "/") for entry in tree)

    # The picker asks for the root it was given instead of hard-coding one.
    listing = await files.list_tree(
        path="", depth=1, absolute=True, include_root=True, session_id=None, user=user, db=None
    )
    assert listing["path"] == str(home)
    assert [entry["name"] for entry in listing["entries"]] == expected

    inside = await files.list_tree(
        path=str(home / "projects"),
        depth=1, absolute=True, include_root=False, session_id=None, user=user, db=None,
    )
    assert inside == []

    # An explicit "/" is still refused; only the implicit default is home.
    with pytest.raises(HTTPException) as exc:
        await files.list_tree(
            path="/", depth=1, absolute=True, include_root=False, session_id=None, user=user, db=None
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_absolute_guard_is_not_a_string_prefix_check(backends, monkeypatch):
    from crabagent.serve.api import files

    home = backends.home  # tmp_path/home
    evil = backends.tmp / "home-evil" / "secret.txt"  # string-prefix sibling
    evil.parent.mkdir()
    evil.write_text("evil\n", encoding="utf-8")
    assert str(evil).startswith(str(home))  # the trap the old check fell into

    with pytest.raises(HTTPException) as exc:
        files._resolve_file_path(str(evil), absolute=True)
    assert exc.value.status_code == 400
    assert "under home" in str(exc.value.detail)

    request, _queue = _fake_request(None)
    with pytest.raises(HTTPException) as exc:
        await files.write_file(
            files.WriteFileRequest(path=str(evil), content="pwned\n", absolute=True),
            request,
            user=_user(),
            db=None,
        )
    assert exc.value.status_code == 400
    assert evil.read_text(encoding="utf-8") == "evil\n"


@pytest.mark.asyncio
async def test_session_workspace_at_root_cannot_write_outside_home(backends, monkeypatch):
    from crabagent.serve.api import files

    home = backends.home
    escaped = backends.tmp / "escaped.txt"  # outside the fake home
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace="/"))
    request, _queue = _fake_request("sess-1")

    with pytest.raises(HTTPException) as exc:
        await files.write_file(
            files.WriteFileRequest(
                path=str(escaped).lstrip("/"), content="pwned\n", session_id="sess-1"
            ),
            request,
            user=_user(),
            db=None,
        )
    assert exc.value.status_code == 403
    assert not escaped.exists()
    assert not (home / "escaped.txt").exists()


@pytest.mark.asyncio
async def test_foreign_session_id_on_write_gets_no_broadcast_and_no_molt(
    backends, molt_factory, monkeypatch
):
    from crabagent.core.database import Molt
    from crabagent.core.event import EventType
    from crabagent.serve.api import files
    from sqlalchemy import func, select

    _patch_conversation(
        monkeypatch, SimpleNamespace(user_id=999, workspace=str(backends.chat))
    )
    request, stream = _fake_request("sess-foreign")
    critical = request.app.state.event_queues["q"][1]
    # An *existing* file: only then does the save try to take a molt snapshot,
    # so a new file would make the "no molt" assertion pass vacuously.
    target = backends.server / "plain.txt"
    target.write_text("before\n", encoding="utf-8")

    result = await files.write_file(
        files.WriteFileRequest(path="plain.txt", content="mine\n", session_id="sess-foreign"),
        request,
        user=_user(),
        db=None,
    )

    # The write itself still succeeds — it just no longer pretends to be that chat.
    assert result["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "mine\n"
    assert [e for e in _drain(critical, stream) if e.type == EventType.FILE_UPDATED] == []
    async with molt_factory() as db:
        total = (await db.execute(select(func.count()).select_from(Molt))).scalar_one()
    assert total == 0


@pytest.mark.asyncio
async def test_image_and_download_reject_paths_outside_home(backends, monkeypatch):
    import crabagent.serve.services.auth as auth_svc
    from crabagent.serve.api import files

    home = backends.home
    outside = backends.tmp / "outside"
    outside.mkdir()
    (outside / "logo.png").write_bytes(b"\x89PNG\x00\x01")

    monkeypatch.setattr(auth_svc, "decode_access_token", lambda token: {"sub": "7"})

    async def _get_user_by_id(db, user_id):
        return _user(int(user_id))

    monkeypatch.setattr(auth_svc, "get_user_by_id", _get_user_by_id)

    with pytest.raises(HTTPException) as exc:
        await files.get_image(path=str(outside / "logo.png"), absolute=True, token="t", db=None)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await files.download_file(path=str(outside / "logo.png"), absolute=True, token="t", db=None)
    assert exc.value.status_code == 400
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("PRIVATE KEY\n", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        await files.download_file(
            path=str(home / ".ssh" / "id_rsa"), absolute=True, token="t", db=None
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_absolute_and_session_relative_paths_inside_home_still_work(backends, monkeypatch):
    from crabagent.serve.api import files

    home = backends.home
    (home / "notes.md").write_text("hello home\n", encoding="utf-8")
    (backends.chat / "chat.md").write_text("hello chat\n", encoding="utf-8")
    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=str(backends.chat)))
    user = _user()
    request, _queue = _fake_request("sess-1")

    read = await files.read_file(
        path=str(home / "notes.md"),
        absolute=True,
        start_line=None,
        end_line=None,
        session_id=None,
        user=user,
        db=None,
    )
    assert read["content"] == "hello home\n"

    written = await files.write_file(
        files.WriteFileRequest(path=str(home / "notes.md"), content="updated\n", absolute=True),
        request,
        user=user,
        db=None,
    )
    assert written["status"] == "ok"
    assert (home / "notes.md").read_text(encoding="utf-8") == "updated\n"

    chat_read = await files.read_file(
        path="chat.md",
        absolute=False,
        start_line=None,
        end_line=None,
        session_id="sess-1",
        user=user,
        db=None,
    )
    assert chat_read["content"] == "hello chat\n"

    chat_written = await files.write_file(
        files.WriteFileRequest(path="chat.md", content="changed\n", session_id="sess-1"),
        request,
        user=user,
        db=None,
    )
    assert chat_written["status"] == "ok"
    assert (backends.chat / "chat.md").read_text(encoding="utf-8") == "changed\n"


@pytest.mark.asyncio
async def test_owned_session_without_a_workspace_keeps_its_event_and_molt(
    backends, molt_factory, monkeypatch
):
    """A fresh chat has Conversation.workspace == "": it still owns its chat.

    Ownership must not be inferred from "a workspace directory exists" — doing
    so silently drops the file_updated event and files the molt row under ''.
    """
    from sqlalchemy import select

    from crabagent.core.database import Molt
    from crabagent.core.event import EventType
    from crabagent.serve.api import files

    _patch_conversation(monkeypatch, SimpleNamespace(user_id=7, workspace=""))
    request, stream = _fake_request("sess-new")
    critical = request.app.state.event_queues["q"][1]
    # A new file has nothing to snapshot, so start from an existing one.
    (backends.server / "fresh.py").write_text("before\n", encoding="utf-8")

    result = await files.write_file(
        files.WriteFileRequest(path="fresh.py", content="x = 1\n", session_id="sess-new"),
        request,
        user=_user(),
        db=None,
    )

    assert result["status"] == "ok"
    # No workspace yet: the save lands in the server workspace ...
    assert (backends.server / "fresh.py").read_text(encoding="utf-8") == "x = 1\n"
    # ... but the session still gets its event and its undo history.
    updates = [e for e in _drain(critical, stream) if e.type == EventType.FILE_UPDATED]
    assert len(updates) == 1
    assert updates[0].data["path"] == "fresh.py"
    assert stream.empty()

    async with molt_factory() as db:
        rows = (await db.execute(select(Molt))).scalars().all()
    assert [row.session_id for row in rows] == ["sess-new"]


@pytest.mark.asyncio
async def test_denied_roots_are_refused_through_relative_paths_from_home(
    backends, monkeypatch
):
    """The default workspace is Path.cwd(), so starting from ~ makes it $HOME.

    The relative branch of _resolve_file_path must apply the deny list, not just
    the absolute branch, or ".ssh/id_rsa" is reachable via download/delete.
    """
    import crabagent.serve.services.auth as auth_svc
    from crabagent.core.config import settings
    from crabagent.serve.api import files

    home = backends.home
    monkeypatch.setattr(settings, "workspace", home)
    (home / ".ssh").mkdir()
    key = home / ".ssh" / "id_rsa"
    key.write_text("PRIVATE KEY\n", encoding="utf-8")
    (home / ".crabagent").mkdir()
    (home / ".crabagent" / "encryption_key").write_text("k\n", encoding="utf-8")

    monkeypatch.setattr(auth_svc, "decode_access_token", lambda token: {"sub": "7"})

    async def _get_user_by_id(db, user_id):
        return _user(int(user_id))

    monkeypatch.setattr(auth_svc, "get_user_by_id", _get_user_by_id)

    for relative in (".ssh/id_rsa", ".crabagent/encryption_key"):
        with pytest.raises(HTTPException) as exc:
            await files.download_file(path=relative, absolute=False, token="t", db=None)
        assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        await files.delete_entry(files.DeleteRequest(path=".crabagent"), user=_user())
    assert exc.value.status_code == 403

    assert key.read_text(encoding="utf-8") == "PRIVATE KEY\n"
    assert (home / ".crabagent" / "encryption_key").exists()


@pytest.mark.asyncio
async def test_serve_dir_pins_only_an_allowed_directory(backends, monkeypatch):
    """The preview token is self-authenticating, so its root must be validated."""
    from pathlib import Path as _Path

    from crabagent.serve.api import files

    home = backends.home
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("PRIVATE KEY\n", encoding="utf-8")
    (home / ".crabagent").mkdir()
    (home / ".crabagent" / "encryption_key").write_text("k\n", encoding="utf-8")
    known_tokens = dict(files._html_servers)

    for blocked in ("/etc", str(home / ".ssh"), str(home / ".crabagent")):
        with pytest.raises(HTTPException) as exc:
            await files.start_preview_session(path=blocked, user=_user())
        assert exc.value.status_code == 403
    # No token was issued, so there is no servable root either.
    assert files._html_servers == known_tokens

    # A directory the /files routes already allow still previews its files.
    site = home / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    session = await files.start_preview_session(path=str(site), user=_user())
    try:
        assert session["token"]
        response = await files.serve_preview_file(
            preview_token=session["token"], file_path="index.html"
        )
        assert _Path(response.path).read_text(encoding="utf-8") == "<h1>hi</h1>"
    finally:
        await files.stop_preview_session(token=session["token"], user=_user())


@pytest.mark.asyncio
async def test_git_endpoints_refuse_a_workspace_outside_the_allowed_roots(backends):
    from crabagent.serve.api import files

    outside = backends.tmp / "someone-elses-repo"
    (outside / ".git").mkdir(parents=True)
    (outside / "secret.py").write_text("token = 'x'\n", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        await files.git_status(workspace=str(outside), user=_user())
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await files.git_diff(path=None, cached=False, workspace=str(outside), user=_user())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_home_tree_does_not_offer_denied_directories(backends):
    from crabagent.serve.api import files

    home = backends.home
    (home / ".crabagent").mkdir()
    (home / ".ssh").mkdir()
    (home / "visible").mkdir()

    tree = await files.list_tree(
        path="", depth=1, absolute=True, include_root=False, session_id=None,
        user=_user(), db=None,
    )
    names = {entry["name"] for entry in tree}
    assert "visible" in names
    # Both would 403 on click, so the tree must not advertise them.
    assert ".crabagent" not in names
    assert ".ssh" not in names


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

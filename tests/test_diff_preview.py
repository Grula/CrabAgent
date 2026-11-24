"""Diff preview shown before a mutating tool call is confirmed."""

from __future__ import annotations

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.diff_preview import build_diff_preview
from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.event import EventBus, EventType


# ── build_diff_preview ────────────────────────────────────────────────


def test_write_new_file_is_all_added(tmp_path):
    preview = build_diff_preview("write", {"path": "notes.txt", "content": "a\nb\n"}, tmp_path)

    assert preview is not None
    assert set(preview) == {"path", "unified", "added", "removed", "truncated", "new_file"}
    assert preview["path"] == "notes.txt"
    assert preview["new_file"] is True
    assert preview["truncated"] is False
    assert preview["added"] == 2
    assert preview["removed"] == 0
    assert preview["unified"].startswith("--- a/notes.txt\n+++ b/notes.txt\n@@ -0,0 +1,2 @@")
    assert "+a" in preview["unified"] and "+b" in preview["unified"]


def test_write_modification_counts(tmp_path):
    (tmp_path / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")

    preview = build_diff_preview("write", {"file_path": "app.py", "content": "one\nTWO\nthree\n"}, tmp_path)

    assert preview is not None
    assert preview["path"] == "app.py"
    assert preview["new_file"] is False
    assert preview["added"] == 1
    assert preview["removed"] == 1
    assert "-two" in preview["unified"]
    assert "+TWO" in preview["unified"]


def test_edit_replace_all(tmp_path):
    (tmp_path / "dup.txt").write_text("x\nx\n", encoding="utf-8")

    preview = build_diff_preview(
        "edit",
        {"path": "dup.txt", "old_string": "x", "new_string": "y", "replace_all": True},
        tmp_path,
    )

    assert preview is not None
    assert preview["added"] == 2
    assert preview["removed"] == 2
    assert preview["new_file"] is False


def test_edit_replaces_first_occurrence_only(tmp_path):
    (tmp_path / "dup.txt").write_text("x\nx\n", encoding="utf-8")

    preview = build_diff_preview("edit", {"path": "dup.txt", "old_string": "x", "new_string": "y"}, tmp_path)

    assert preview is not None
    assert preview["added"] == 1
    assert preview["removed"] == 1


def test_edit_with_missing_old_string_returns_none(tmp_path):
    (tmp_path / "app.py").write_text("hello\n", encoding="utf-8")

    assert build_diff_preview("edit", {"path": "app.py", "old_string": "absent", "new_string": "x"}, tmp_path) is None


def test_binary_file_returns_none(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    assert build_diff_preview("write", {"path": "logo.png", "content": "text\n"}, tmp_path) is None


def test_file_over_one_megabyte_returns_none(tmp_path):
    (tmp_path / "huge.txt").write_bytes(b"a" * 1_000_001)

    assert build_diff_preview("write", {"path": "huge.txt", "content": "small\n"}, tmp_path) is None


def test_large_diff_is_truncated(tmp_path):
    content = "\n".join(f"line {i}" for i in range(500)) + "\n"

    preview = build_diff_preview("write", {"path": "big.txt", "content": content}, tmp_path)

    assert preview is not None
    assert preview["truncated"] is True
    assert preview["unified"].endswith("... [diff truncated]")
    assert len(preview["unified"].splitlines()) == 401
    # Counts describe the whole change, not the truncated text.
    assert preview["added"] == 500
    assert preview["removed"] == 0


def test_bash_call_returns_none(tmp_path):
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")

    assert build_diff_preview("bash", {"command": "echo hi", "path": "f.txt", "content": "b\n"}, tmp_path) is None


# ── confirmation payload ──────────────────────────────────────────────


def _confirming_context(tmp_path, confirm):
    registry = ToolRegistry()

    @registry.register(
        name="write",
        description="write a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        requires_permission=True,
    )
    def _write(path: str, content: str, context=None):
        return f"wrote {path}"

    @registry.register(
        name="read_file",
        description="read a file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    def _read_file(path: str, context=None):
        return "content"

    context = AgentContext(
        workspace=tmp_path,
        tool_registry=registry,
        tool_permissions={"write": "confirm"},
    )
    context.confirm_callback = confirm
    return registry, context


@pytest.mark.asyncio
async def test_confirmation_payload_carries_diff_for_write_only(tmp_path):
    (tmp_path / "notes.txt").write_text("old\n", encoding="utf-8")
    seen: list[tuple[str, dict | None]] = []

    async def confirm(tool_name: str, args: dict, diff_preview: dict | None = None) -> bool:
        seen.append((tool_name, diff_preview))
        return True

    registry, context = _confirming_context(tmp_path, confirm)

    await registry.execute("write", {"path": "notes.txt", "content": "new\n"}, context=context)
    await registry.execute("read_file", {"path": "notes.txt"}, context=context)

    assert [name for name, _ in seen] == ["write"]
    preview = seen[0][1]
    assert preview is not None
    assert preview["path"] == "notes.txt"
    assert preview["added"] == 1 and preview["removed"] == 1
    assert "-old" in preview["unified"] and "+new" in preview["unified"]


@pytest.mark.asyncio
async def test_legacy_two_arg_confirm_callback_still_works(tmp_path):
    (tmp_path / "notes.txt").write_text("old\n", encoding="utf-8")
    seen: list[str] = []

    async def confirm(tool_name: str, args: dict) -> bool:
        seen.append(tool_name)
        return True

    registry, context = _confirming_context(tmp_path, confirm)

    result = await registry.execute("write", {"path": "notes.txt", "content": "new\n"}, context=context)

    assert seen == ["write"]
    assert result == "wrote notes.txt"


@pytest.mark.asyncio
async def test_confirm_event_includes_diff_preview_only_when_present(tmp_path, monkeypatch):
    from crabagent.serve.api import confirm as confirm_mod

    async def _noop_persist(*args, **kwargs):
        return None

    async def _noop_create_request(*args, **kwargs):
        return None

    class _FakeSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    import crabagent.core.database as database
    import crabagent.core.push as push
    import crabagent.core.task.request_service as request_service

    monkeypatch.setattr(confirm_mod, "_persist_confirmation", _noop_persist)
    monkeypatch.setattr(database, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(request_service, "create_request", _noop_create_request)
    monkeypatch.setattr(push, "notify_request_created", _noop_persist)

    bus = EventBus(name="test")
    events = []
    bus.subscribe(lambda event: events.append(event))

    preview = build_diff_preview("write", {"path": "notes.txt", "content": "new\n"}, tmp_path)
    assert preview is not None

    future = await confirm_mod.request_confirmation(
        bus, "session-1", "write", {"path": "notes.txt", "content": "new\n"}, diff_preview=preview
    )
    future.cancel()

    assert len(events) == 1
    assert events[0].type is EventType.TOOL_CONFIRM_REQUEST
    payload = events[0].data
    assert set(payload) == {"confirm_id", "tool_name", "args_summary", "diff_preview"}
    assert payload["tool_name"] == "write"
    assert payload["diff_preview"] == preview

    events.clear()
    future = await confirm_mod.request_confirmation(bus, "session-1", "read_file", {"path": "notes.txt"})
    future.cancel()

    # No preview → the key is absent, so existing clients see the old payload.
    assert set(events[0].data) == {"confirm_id", "tool_name", "args_summary"}


# ── persisted confirmation (session reload) ──────────────────────────


@pytest.fixture
async def confirm_db(tmp_path, monkeypatch):
    """A real SQLite database wired into ``database.async_session_factory``."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import crabagent.core.database as database

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'confirm.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    yield factory
    await engine.dispose()


async def _add_conversation(factory, session_id: str) -> int:
    import crabagent.core.database as database

    async with factory() as db:
        conversation = database.Conversation(
            session_id=session_id, user_id=1, title="Confirm", active_branch="main"
        )
        db.add(conversation)
        await db.commit()
        return conversation.id


async def _reload_cards(factory, conversation_id: int) -> list[dict]:
    """Load messages exactly like GET /sessions/{id}/messages does."""
    import crabagent.core.database as database
    from crabagent.serve.services.message import get_messages, message_to_response

    async with factory() as db:
        msgs = await get_messages(db, conversation_id, branch_id="main")
    assert all(isinstance(m, database.Message) for m in msgs)
    return [message_to_response(m) for m in msgs]


@pytest.mark.asyncio
async def test_persisted_confirmation_reloads_with_diff_preview(tmp_path, confirm_db):
    from crabagent.serve.api.confirm import _persist_confirmation

    conversation_id = await _add_conversation(confirm_db, "session-diff")
    (tmp_path / "notes.txt").write_text("old\n", encoding="utf-8")
    preview = build_diff_preview("write", {"path": "notes.txt", "content": "new\n"}, tmp_path)
    assert preview is not None

    await _persist_confirmation("session-diff", "confirm-abc", "write", '{"path": "notes.txt"}', preview)

    cards = await _reload_cards(confirm_db, conversation_id)
    assert len(cards) == 1
    card = cards[0]
    assert card["role"] == "tool_confirm"
    assert card["tool_call_id"] == "confirm-abc"
    assert set(card["diff_preview"]) == {"path", "unified", "added", "removed", "truncated", "new_file"}
    assert card["diff_preview"] == preview
    assert card["diff_preview"]["unified"] == preview["unified"]
    assert card["diff_preview"]["added"] == 1 and card["diff_preview"]["removed"] == 1


@pytest.mark.asyncio
async def test_persisted_confirmation_without_diff_has_no_key(confirm_db):
    from crabagent.serve.api.confirm import _persist_confirmation

    conversation_id = await _add_conversation(confirm_db, "session-plain")

    await _persist_confirmation("session-plain", "confirm-plain", "bash", '{"command": "ls"}')

    cards = await _reload_cards(confirm_db, conversation_id)
    assert len(cards) == 1
    assert "diff_preview" not in cards[0]
    assert '"status": "pending"' in cards[0]["content"]


@pytest.mark.asyncio
async def test_reloaded_confirmation_with_null_or_invalid_diff_has_no_key(confirm_db):
    import crabagent.core.database as database

    conversation_id = await _add_conversation(confirm_db, "session-broken")
    async with confirm_db() as db:
        db.add_all(
            [
                database.Message(
                    conversation_id=conversation_id,
                    sequence=1,
                    role="tool_confirm",
                    content='{"args_summary": "{}", "status": "pending"}',
                    name="write",
                    tool_call_id="confirm-null",
                    diff_preview=None,
                ),
                database.Message(
                    conversation_id=conversation_id,
                    sequence=2,
                    role="tool_confirm",
                    content='{"args_summary": "{}", "status": "pending"}',
                    name="write",
                    tool_call_id="confirm-invalid",
                    diff_preview="{not json",
                ),
                database.Message(
                    conversation_id=conversation_id,
                    sequence=3,
                    role="tool_confirm",
                    content='{"args_summary": "{}", "status": "pending"}',
                    name="write",
                    tool_call_id="confirm-list",
                    diff_preview="[]",
                ),
            ]
        )
        await db.commit()

    cards = await _reload_cards(confirm_db, conversation_id)

    assert [c["tool_call_id"] for c in cards] == ["confirm-null", "confirm-invalid", "confirm-list"]
    for card in cards:
        assert "diff_preview" not in card


@pytest.mark.asyncio
async def test_init_db_adds_diff_preview_column_to_existing_database(tmp_path, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    import crabagent.core.database as database

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    # A pre-existing database whose messages table predates the column.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, "
                "sequence INTEGER NOT NULL DEFAULT 0, role VARCHAR(20) NOT NULL, content TEXT, "
                "tool_calls TEXT, tool_call_id VARCHAR(100), name VARCHAR(100), "
                "reasoning_content TEXT, created_at DATETIME)"
            )
        )
        result = await conn.execute(text("PRAGMA table_info(messages)"))
        assert "diff_preview" not in [row[1] for row in result.fetchall()]

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "_ensure_workspace_dirs", lambda: None)
    monkeypatch.setattr(database, "_migrate_db_to_home", lambda: None)
    await database.init_db()

    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(messages)"))
        assert "diff_preview" in [row[1] for row in result.fetchall()]
    await engine.dispose()

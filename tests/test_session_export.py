import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crabagent.core import database as db
from crabagent.serve.api.session import router
from crabagent.serve.deps import get_current_user

BASE64_IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


@pytest.fixture
async def client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)
    async with factory() as session:
        mine, other, empty, branched, mentions, malformed = (
            db.Conversation(session_id="mine", user_id=1, title="Deploy Notes: 2026", active_branch="main"),
            db.Conversation(session_id="other", user_id=2, title="Not Yours", active_branch="main"),
            db.Conversation(session_id="empty", user_id=1, title="", active_branch="main"),
            db.Conversation(session_id="branched", user_id=1, title="Branched", active_branch="main"),
            db.Conversation(session_id="mentions", user_id=1, title="Mentions", active_branch="main"),
            db.Conversation(session_id="malformed", user_id=1, title="Malformed", active_branch="main"),
        )
        session.add_all([mine, other, empty, branched, mentions, malformed])
        await session.flush()
        session.add_all(
            [
                # Seeded out of role order on purpose: sequence must win over grouping.
                db.Message(conversation_id=mine.id, sequence=3, role="user", content="third?"),
                db.Message(conversation_id=mine.id, sequence=1, role="user", content="first question"),
                db.Message(conversation_id=mine.id, sequence=2, role="assistant", content="first answer"),
                db.Message(
                    conversation_id=mine.id,
                    sequence=4,
                    role="assistant",
                    content=f"here is a chart ![chart]({BASE64_IMAGE})",
                ),
                db.Message(conversation_id=mine.id, sequence=5, role="agent_switch", content='{"agent": "coder"}'),
                db.Message(conversation_id=mine.id, sequence=6, role="screenshot", content="/tmp/shot.png"),
                db.Message(conversation_id=mine.id, sequence=7, role="compress", content="summary of earlier turns"),
                db.Message(conversation_id=mine.id, sequence=8, role="workspace", content="moved to /tmp"),
                db.Message(conversation_id=mine.id, sequence=9, role="experience", content="lesson learned"),
                db.Message(
                    conversation_id=mine.id,
                    sequence=10,
                    role="sub_agent",
                    name="coder",
                    content='{"agent_name": "coder", "display_name": "Coder", "text": "done", "elapsed": 2}',
                ),
                # User rows wrapped in a delegation instruction (@mention): the chat bubble
                # shows only the user's own words, so the export must too.
                db.Message(
                    conversation_id=mentions.id,
                    sequence=1,
                    role="user",
                    content='[delegate_task] agent_name="coder" task="do it"\n\nOriginal request: fix the parser',
                ),
                db.Message(
                    conversation_id=mentions.id,
                    sequence=2,
                    role="user",
                    content='[delegate_task] agent_name="coder" task="do it"\n\nOriginal request: @reviewer please check\nline 3',
                ),
                db.Message(
                    conversation_id=mentions.id,
                    sequence=3,
                    role="user",
                    content='[delegate_task] agent_name="coder" task="do it"',
                ),
                db.Message(
                    conversation_id=mentions.id,
                    sequence=4,
                    role="user",
                    content='[delegate_parallel] tasks=[{"agent_name":"a","task":"t"},{"agent_name":"b","task":"t"}]',
                ),
                db.Message(
                    conversation_id=mentions.id,
                    sequence=5,
                    role="user",
                    content='[delegate_parallel] tasks=[{"agent_name":"a","task":"t1"},{"agent_name":"b","task":"t2"}]',
                ),
                db.Message(conversation_id=mentions.id, sequence=6, role="user", content="hello @coder can you look at this?"),
                # Wrapper with an empty original text: nothing left to show, so the turn is skipped.
                db.Message(
                    conversation_id=mentions.id,
                    sequence=7,
                    role="user",
                    content='[delegate_task] agent_name="coder" task="do it"\n\nOriginal request: ',
                ),
                # Unparseable wrappers stay as they are rather than vanishing from the export.
                db.Message(
                    conversation_id=malformed.id,
                    sequence=1,
                    role="user",
                    content='[delegate_task] agent_name="coder" task="unterminated',
                ),
                db.Message(conversation_id=malformed.id, sequence=2, role="user", content="[delegate_parallel] tasks=[not json]"),
                # Branching copies messages onto the new branch at fresh sequences;
                # only one branch is visible in the chat view at a time.
                db.Message(conversation_id=branched.id, sequence=1, role="user", content="OPENING-v1", branch_id="main"),
                db.Message(conversation_id=branched.id, sequence=2, role="assistant", content="ANSWER-v1", branch_id="main"),
                db.Message(conversation_id=branched.id, sequence=3, role="user", content="OPENING-v1", branch_id="edit"),
                db.Message(
                    conversation_id=branched.id,
                    sequence=4,
                    role="assistant",
                    content="ANSWER-v2-edited",
                    branch_id="edit",
                ),
            ]
        )
        await session.commit()

    async def database():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[db.get_db] = database
    app.dependency_overrides[get_current_user] = lambda: db.User(id=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


def _disposition(response) -> str:
    return response.headers["content-disposition"]


async def test_export_is_markdown_with_title_filename(client):
    response = await client.get("/sessions/mine/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "charset=utf-8" in response.headers["content-type"]
    assert 'attachment; filename="Deploy-Notes-2026.md"' == _disposition(response)
    body = response.text
    assert body.startswith("# Deploy Notes: 2026")
    assert "- Session: `mine`" in body


async def test_export_falls_back_to_session_id_filename_for_empty_title(client):
    response = await client.get("/sessions/empty/export")
    assert response.status_code == 200
    assert 'attachment; filename="chat-empty.md"' == _disposition(response)
    assert response.text.startswith("# Conversation")


async def test_export_is_chronological(client):
    body = (await client.get("/sessions/mine/export")).text
    order = [
        body.index("first question"),
        body.index("first answer"),
        body.index("third?"),
        body.index("here is a chart"),
    ]
    assert order == sorted(order)
    assert "## User\n\nfirst question" in body
    assert "## Assistant\n\nfirst answer" in body


async def test_export_strips_base64_images(client):
    body = (await client.get("/sessions/mine/export")).text
    assert "iVBORw0KGgo" not in body
    assert BASE64_IMAGE not in body
    assert "[image]" in body


async def test_export_skips_internal_roles(client):
    body = (await client.get("/sessions/mine/export")).text
    for leaked in ('{"agent": "coder"}', "/tmp/shot.png", "summary of earlier turns", "moved to /tmp", "lesson learned"):
        assert leaked not in body
    assert "### Coder (2s)\n\ndone" in body


async def test_export_empty_conversation_still_returns_document(client):
    response = await client.get("/sessions/empty/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("# Conversation")
    assert response.text.rstrip().endswith("*Exported from CrabAgent*")


async def test_export_unknown_session_is_404(client):
    assert (await client.get("/sessions/missing/export")).status_code == 404


async def test_export_other_users_session_is_403(client):
    assert (await client.get("/sessions/other/export")).status_code == 403


async def test_export_only_includes_active_branch(client):
    """A branched conversation must export the turns the chat view shows, once each."""
    body = (await client.get("/sessions/branched/export")).text
    assert body.count("OPENING-v1") == 1
    assert "ANSWER-v1" in body
    assert "ANSWER-v2-edited" not in body

    # Same conversation, other branch active: the export follows it.
    async with db.async_session_factory() as session:
        conv = (
            await session.execute(select(db.Conversation).where(db.Conversation.session_id == "branched"))
        ).scalar_one()
        conv.active_branch = "edit"
        await session.commit()

    body = (await client.get("/sessions/branched/export")).text
    assert body.count("OPENING-v1") == 1
    assert "ANSWER-v2-edited" in body
    assert "ANSWER-v1" not in body
    assert body.index("OPENING-v1") < body.index("ANSWER-v2-edited")


def _user_turns(body: str) -> list[str]:
    """Body of each "## User" turn, in export order (the mentions fixture has only user turns)."""
    turns, _, _ = body.partition("\n---\n\n*Exported from CrabAgent*")
    return [part.strip() for part in turns.split("## User\n\n")[1:]]


async def test_export_user_message_shows_the_original_request(client):
    body = (await client.get("/sessions/mentions/export")).text
    turns = _user_turns(body)
    assert "fix the parser" in turns
    # No internal delegation instruction leaks into any turn.
    assert "[delegate_task]" not in body
    assert "[delegate_parallel]" not in body
    assert "agent_name=" not in body


async def test_export_keeps_the_users_own_mentions_and_line_breaks(client):
    body = (await client.get("/sessions/mentions/export")).text
    assert "@reviewer please check\nline 3" in _user_turns(body)


async def test_export_falls_back_to_delegate_task_mention_form(client):
    body = (await client.get("/sessions/mentions/export")).text
    assert "@coder do it" in _user_turns(body)


async def test_export_parallel_delegation_with_one_task_name_collapses(client):
    body = (await client.get("/sessions/mentions/export")).text
    assert "@a @b t" in _user_turns(body)


async def test_export_parallel_delegation_with_different_tasks_lists_each(client):
    body = (await client.get("/sessions/mentions/export")).text
    assert "@a: t1; @b: t2" in _user_turns(body)


async def test_export_plain_user_message_is_unchanged(client):
    body = (await client.get("/sessions/mentions/export")).text
    assert "hello @coder can you look at this?" in _user_turns(body)


async def test_export_skips_a_user_turn_with_no_visible_text(client):
    """The wrapper is dropped, nothing is left to show, so the turn is not exported."""
    body = (await client.get("/sessions/mentions/export")).text
    assert len(_user_turns(body)) == 6
    assert not any(turn == "" for turn in _user_turns(body))


async def test_export_leaves_malformed_delegation_unchanged(client):
    body = (await client.get("/sessions/malformed/export")).text
    assert '[delegate_task] agent_name="coder" task="unterminated' in _user_turns(body)
    assert "[delegate_parallel] tasks=[not json]" in _user_turns(body)

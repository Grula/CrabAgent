from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from crabagent.core.database import BrowserTask, Conversation, Message, User, WorkspacePreference, get_db
from crabagent.core.timeutil import iso_utc
from crabagent.serve.api.prompt import _tasks
from crabagent.serve.deps import get_current_user, get_owned_conversation
from crabagent.serve.services import conversation as conv_svc
from crabagent.serve.services.message import get_messages, message_to_dict

router = APIRouter(prefix="/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    title: str = ""
    workspace: str = ""
    model: str = ""


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    history_searchable: bool | None = None


class CompressSessionRequest(BaseModel):
    model: str = ""
    provider: str = ""


class CompressSessionResponse(BaseModel):
    summary: str
    model: str
    provider: str
    original_count: int


class SessionResponse(BaseModel):
    session_id: str
    title: str
    workspace: str
    model: str
    provider: str = ""
    agent: str = "default"
    active_branch: str = "main"
    history_searchable: bool = True
    created_at: str | None = None
    updated_at: str | None = None


def _conv_to_response(conv) -> SessionResponse:
    return SessionResponse(
        session_id=conv.session_id,
        title=conv.title,
        workspace=conv.workspace,
        model=conv.model,
        provider=getattr(conv, "provider", "") or "",
        agent=getattr(conv, "agent", None) or "default",
        active_branch=conv.active_branch or "main",
        history_searchable=getattr(conv, "history_searchable", True),
        created_at=iso_utc(conv.created_at),
        updated_at=iso_utc(conv.updated_at),
    )


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    workspace: str | None = Query(None, description="Filter by workspace path"),
    browser_only: bool = Query(False, description="Only sessions with collaboration browser tasks"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convs = await conv_svc.list_conversations(db, user.id, workspace=workspace)
    if browser_only:
        task_sessions = await db.execute(
            select(BrowserTask.session_id).where(BrowserTask.user_id == user.id).distinct()
        )
        allowed_ids = set(task_sessions.scalars())
        convs = [conv for conv in convs if conv.session_id in allowed_ids]
    return [_conv_to_response(c) for c in convs]


class SearchResultItem(BaseModel):
    session_id: str
    title: str
    snippet: str
    role: str
    updated_at: str | None = None


@router.post("/{session_id}/compress", response_model=CompressSessionResponse)
async def compress_session(
    session_id: str,
    req: CompressSessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Persist a user-triggered compression using the normal compression pipeline."""
    if session_id in _tasks and not _tasks[session_id].done():
        raise HTTPException(status_code=409, detail="Session is already processing a prompt")

    conv = await get_owned_conversation(db, session_id, user)
    branch_id = conv.active_branch or "main"
    records = await get_messages(db, conv.id, branch_id=branch_id)
    records = [msg for msg in records if msg.role not in ("stats",)]
    if len(records) < 2:
        raise HTTPException(status_code=400, detail="Not enough messages to compress")

    from crabagent.core.agent.compress import compress_context
    from crabagent.core.agent.context import AgentContext
    from crabagent.core.agent.loop import _litellm_params, _resolve_provider
    from crabagent.core.config import settings
    from crabagent.core.proxy import resolve_llm_proxy
    from crabagent.serve.services.persistence import PersistenceListener

    provider_name = req.provider or conv.provider or None
    provider = await _resolve_provider(provider_name)
    proxy = await resolve_llm_proxy(provider)
    model_name = req.model or conv.model or settings.default_model
    model = model_name
    if "/" not in model:
        from crabagent.core.provider_store import model_prefix_for

        model = f"{model_prefix_for(provider.provider_type)}/{model}"

    # Manual compression must use the same context slicing, summarization,
    # persistence, and failure handling as automatic compression.
    context = AgentContext(
        workspace=Path(conv.workspace) if conv.workspace else Path.cwd(),
        messages=[message_to_dict(msg) for msg in records],
        system_prompt=conv.system_prompt or "",
    )
    persistence = PersistenceListener(conversation_id=conv.id, branch_id=branch_id)
    context.metadata["_persistence"] = persistence
    await compress_context(context, _litellm_params(provider, proxy), model)

    summary_message = next((msg for msg in context.messages if msg.get("role") == "compress"), None)
    if not summary_message:
        raise HTTPException(status_code=502, detail="Context compression failed; original messages were kept")

    # The next prompt restores this value into AgentContext before checking the
    # automatic compression threshold. The manual summary replaces that context,
    # so retaining the pre-compression token count would trigger a needless
    # second compression on the next turn.
    conv.tokens = 0
    await db.commit()
    summary = summary_message["content"]

    return CompressSessionResponse(
        summary=summary,
        model=model_name,
        provider=provider.name,
        original_count=len(records),
    )


@router.get("/search", response_model=list[SearchResultItem])
async def search_sessions(
    q: str = Query(..., min_length=1, description="Search keyword"),
    workspace: str | None = Query(None, description="Filter by workspace path"),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full-text search across session titles and message content.

    Uses jieba-tokenized FTS5 (messages_fts_cjk) for Chinese + English,
    with LIKE fallback for edge cases.
    """
    seen = set()
    items = []

    # Tokenize query with jieba for CJK-aware FTS5 search
    from crabagent.core.fts import segment as _segment

    tokenized_q = _segment(q).strip() or q
    # Use prefix matching (*) so partial token search works
    # e.g. "project" -> "project*" matches the token "projectmanagement"
    fts_query = " ".join(t + "*" for t in tokenized_q.split()) if tokenized_q else tokenized_q

    # 1) FTS5-CJK search (jieba-tokenized, handles Chinese + English)
    ws_filter = "AND c.workspace = :workspace" if workspace else ""
    cjk_sql = text(f"""
        SELECT c.session_id, c.title, m.content, m.role, c.updated_at
        FROM messages_fts_cjk
        JOIN messages m ON messages_fts_cjk.rowid = m.id
        JOIN conversations c ON m.conversation_id = c.id
        WHERE messages_fts_cjk MATCH :q
          AND c.user_id = :user_id
          AND c.history_searchable = 1
          {ws_filter}
          AND m.role IN ('user', 'assistant')
          AND m.compressed = 0
        ORDER BY rank
        LIMIT :lim
    """)
    cjk_params: dict = {"q": fts_query, "user_id": user.id, "lim": limit}
    if workspace:
        cjk_params["workspace"] = workspace
    try:
        result = await db.execute(cjk_sql, cjk_params)
        cjk_rows = result.fetchall()
    except Exception:
        cjk_rows = []

    # 2) LIKE search (catches anything FTS5 misses)
    like_sql = text(f"""
        SELECT c.session_id, c.title, m.content, m.role, c.updated_at
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE c.user_id = :user_id
          AND c.history_searchable = 1
          AND m.content LIKE :like_q
          {ws_filter}
          AND m.role IN ('user', 'assistant')
          AND m.compressed = 0
        ORDER BY c.updated_at DESC
        LIMIT :lim
    """)
    like_params: dict = {"like_q": f"%{q}%", "user_id": user.id, "lim": limit}
    if workspace:
        like_params["workspace"] = workspace
    try:
        result = await db.execute(like_sql, like_params)
        like_rows = result.fetchall()
    except Exception:
        like_rows = []

    # 3) Merge: FTS5 first (ranked), then LIKE (deduplicated)
    for row in cjk_rows + like_rows:
        if len(items) >= limit:
            break
        session_id = row[0]
        if session_id in seen:
            continue
        seen.add(session_id)
        content = row[2] or ""
        role = row[3] or "user"
        # Extract plain text from possible JSON content_blocks
        plain = content
        if content.startswith("[") and content.endswith("]"):
            try:
                import json as _j

                blocks = _j.loads(content)
                texts = []
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "text":
                        texts.append(b.get("text", ""))
                plain = " ".join(texts)
            except Exception:
                pass
        # Build snippet around match
        idx = plain.lower().find(q.lower())
        if idx >= 0:
            start = max(0, idx - 40)
            end = min(len(plain), idx + len(q) + 40)
            snippet = ("…" if start > 0 else "") + plain[start:end] + ("…" if end < len(plain) else "")
        else:
            snippet = plain[:100]
        items.append(
            SearchResultItem(
                session_id=session_id,
                title=row[1] or "",
                snippet=snippet,
                role=role,
                updated_at=str(row[4]) if row[4] else None,
            )
        )

    # Also search titles directly (supplements FTS5 results)
    title_sql = text(f"""
        SELECT session_id, title, updated_at FROM conversations
        WHERE user_id = :user_id
          AND history_searchable = 1
          AND title LIKE :like_q
          {"AND workspace = :workspace" if workspace else ""}
        ORDER BY updated_at DESC
        LIMIT :lim
    """)
    title_params: dict = {"like_q": f"%{q}%", "user_id": user.id, "lim": limit}
    if workspace:
        title_params["workspace"] = workspace
    result = await db.execute(title_sql, title_params)
    for row in result.fetchall():
        if row[0] not in seen:
            items.append(
                SearchResultItem(
                    session_id=row[0],
                    title=row[1] or "",
                    snippet="",
                    role="",
                    updated_at=str(row[2]) if row[2] else None,
                )
            )

    return items[:limit]


class WorkspaceInfo(BaseModel):
    workspace: str
    session_count: int
    last_active: str | None = None
    hidden: bool = False
    pinned: bool = False
    sort_order: int = 0


class UpdateWorkspacePreferenceRequest(BaseModel):
    hidden: bool | None = None
    pinned: bool | None = None
    sort_order: int | None = None
    current_workspace: str = ""


class ReorderWorkspacesRequest(BaseModel):
    workspaces: list[str]


async def _workspace_infos(db: AsyncSession, user_id: int) -> list[WorkspaceInfo]:
    result = await db.execute(
        select(Conversation.workspace, Conversation.session_id, Conversation.updated_at)
        .where(Conversation.user_id == user_id, Conversation.workspace != "")
        .order_by(Conversation.updated_at.desc())
    )
    preferences = await db.execute(select(WorkspacePreference).where(WorkspacePreference.user_id == user_id))
    by_workspace = {pref.workspace: pref for pref in preferences.scalars()}
    seen: dict[str, WorkspaceInfo] = {}
    default_order: dict[str, int] = {}
    for workspace, _, updated_at in result.fetchall():
        if workspace in seen:
            seen[workspace].session_count += 1
            continue
        default_order[workspace] = len(default_order)
        pref = by_workspace.get(workspace)
        seen[workspace] = WorkspaceInfo(
            workspace=workspace,
            session_count=1,
            last_active=iso_utc(updated_at),
            hidden=pref.hidden if pref else False,
            pinned=pref.pinned if pref else False,
            sort_order=pref.sort_order if pref else 0,
        )
    return sorted(
        seen.values(),
        key=lambda ws: (
            not ws.pinned,
            ws.sort_order if ws.workspace in by_workspace else 100000 + default_order[ws.workspace],
        ),
    )


@router.get("/workspaces", response_model=list[WorkspaceInfo])
async def list_workspaces(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _workspace_infos(db, user.id)


@router.patch("/workspace-preferences/{workspace:path}", response_model=WorkspaceInfo)
async def update_workspace_preference(
    workspace: str,
    req: UpdateWorkspacePreferenceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not workspace:
        raise HTTPException(status_code=400, detail="Workspace is required")
    current_workspace = req.current_workspace or str(Path.cwd().resolve())
    if req.hidden and workspace == current_workspace:
        raise HTTPException(status_code=400, detail="The current workspace cannot be hidden")

    result = await db.execute(
        select(WorkspacePreference).where(
            WorkspacePreference.user_id == user.id,
            WorkspacePreference.workspace == workspace,
        )
    )
    preference = result.scalar_one_or_none()
    if preference is None:
        preference = WorkspacePreference(user_id=user.id, workspace=workspace)
        db.add(preference)
    for field in ("hidden", "pinned", "sort_order"):
        value = getattr(req, field)
        if value is not None:
            setattr(preference, field, value)
    await db.commit()

    for item in await _workspace_infos(db, user.id):
        if item.workspace == workspace:
            return item
    raise HTTPException(status_code=404, detail="Workspace not found")


@router.put("/workspace-preferences/reorder", response_model=list[WorkspaceInfo])
async def reorder_workspaces(
    req: ReorderWorkspacesRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = {item.workspace for item in await _workspace_infos(db, user.id)}
    if len(req.workspaces) != len(set(req.workspaces)) or not set(req.workspaces).issubset(existing):
        raise HTTPException(status_code=400, detail="Invalid workspace order")

    preferences = await db.execute(select(WorkspacePreference).where(WorkspacePreference.user_id == user.id))
    by_workspace = {pref.workspace: pref for pref in preferences.scalars()}
    for index, workspace in enumerate(req.workspaces):
        preference = by_workspace.get(workspace)
        if preference is None:
            preference = WorkspacePreference(user_id=user.id, workspace=workspace)
            db.add(preference)
        preference.sort_order = index
    await db.commit()
    return await _workspace_infos(db, user.id)


@router.get("/current-workspace")
async def get_current_workspace():
    return {"workspace": str(Path.cwd().resolve())}


@router.get("/project-memory")
async def get_project_memory(
    user: User = Depends(get_current_user),
    workspace: str = Query("", description="Workspace path. Defaults to CWD."),
):
    """Return aggregated project memory for the given workspace."""
    ws = Path(workspace).resolve() if workspace else Path.cwd().resolve()
    from crabagent.core.project_memory import load_project_memory

    pm = await load_project_memory(user.id, ws)
    if pm is None:
        return {
            "workspace": str(ws),
            "tech_stack": [],
            "recent_lessons": [],
            "lesson_count": 0,
            "last_active": "",
        }
    return {
        "workspace": pm.workspace,
        "tech_stack": pm.tech_stack,
        "recent_lessons": pm.recent_lessons,
        "lesson_count": pm.lesson_count,
        "last_active": pm.last_active,
    }


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    req: CreateSessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await conv_svc.create_conversation(
        db,
        user_id=user.id,
        workspace=req.workspace,
        model=req.model,
        title=req.title,
    )
    return _conv_to_response(conv)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await get_owned_conversation(db, session_id, user)
    return _conv_to_response(conv)


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    req: UpdateSessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_conversation(db, session_id, user)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    conv = await conv_svc.update_conversation(db, session_id, **updates)
    return _conv_to_response(conv)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_conversation(db, session_id, user)
    await conv_svc.delete_conversation(db, session_id)


@router.get("/{session_id}/report", response_class=PlainTextResponse)
async def get_session_report(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await get_owned_conversation(db, session_id, user)
    result = await db.execute(select(Message).where(Message.conversation_id == conv.id).order_by(Message.id))
    msgs = result.scalars().all()

    lines = [f"# {conv.title or 'Conversation Report'}\n"]
    lines.append(f"Session: {session_id}")
    lines.append(f"Created: {iso_utc(conv.created_at) or 'N/A'}\n")

    user_msgs = [m for m in msgs if m.role == "user"]
    assistant_msgs = [m for m in msgs if m.role == "assistant"]
    sub_agent_msgs = [m for m in msgs if m.role == "sub_agent"]

    for m in user_msgs:
        lines.append(f"## User\n\n{m.content or ''}\n")

    for m in sub_agent_msgs:
        try:
            data = json.loads(m.content)
            name = data.get("display_name") or data.get("agent_name") or m.name or "Agent"
            text = data.get("text", m.content)
            elapsed = data.get("elapsed")
            tokens = data.get("tokens")
            iterations = data.get("iterations")
            meta = []
            if elapsed is not None:
                meta.append(f"{elapsed}s")
            if tokens is not None:
                meta.append(f"{tokens} tokens")
            if iterations is not None:
                meta.append(f"{iterations} steps")
            meta_str = f" ({', '.join(meta)})" if meta else ""
            lines.append(f"### {name}{meta_str}\n\n{text}\n")
        except (json.JSONDecodeError, TypeError):
            lines.append(f"### {m.name or 'Agent'}\n\n{m.content or ''}\n")

    for m in assistant_msgs:
        if m.content:
            lines.append(f"## Assistant\n\n{m.content}\n")

    lines.append("---\n")
    lines.append("*Generated by CrabAgent*")
    return "\n".join(lines)


# Base64 image payloads (inline markdown images, data URLs in JSON block content).
_DATA_URL_RE = re.compile(r"data:[^;,\s]*;base64,[A-Za-z0-9+/=]+")

_EXPORT_HEADINGS = {"user": "## User", "assistant": "## Assistant"}


def _export_filename(title: str, session_id: str) -> str:
    """ASCII-only download name, no RFC 5987 encoding needed."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title or "").strip("-")[:60].strip("-")
    return f"{slug or 'chat-' + session_id[:8]}.md"


def _export_text(content: str) -> str:
    """Readable text for one message: no base64 payloads, no JSON block dumps."""
    raw = content or ""
    if raw.lstrip().startswith("["):
        try:
            blocks = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            blocks = None
        if isinstance(blocks, list) and blocks and all(
            isinstance(b, dict) and b.get("type") in ("text", "image_url") for b in blocks
        ):
            parts = ["[image]" if b["type"] == "image_url" else b.get("text", "") for b in blocks]
            raw = "\n\n".join(p for p in parts if isinstance(p, str) and p.strip())
    return _DATA_URL_RE.sub("[image]", raw).strip()


_DELEGATE_MARKER = "\n\nOriginal request: "
_DELEGATE_TASK_RE = re.compile(r'^\[delegate_task\] agent_name="([^"]+)" task="([\s\S]*)"\Z')
_DELEGATE_PARALLEL_RE = re.compile(r"^\[delegate_parallel\] tasks=(\[[\s\S]*\])\Z")


def _export_user_text(content: str) -> str:
    """What the user typed, as the chat bubble shows it (mirror of frontend displayUserText).

    A message sent with an @mention is stored wrapped in a delegation instruction;
    the export keeps the user's own words instead of the internal one.
    """
    raw = content or ""
    if not raw.startswith("[delegate_task]") and not raw.startswith("[delegate_parallel]"):
        return raw
    marker = raw.find(_DELEGATE_MARKER)
    if marker >= 0:
        return raw[marker + len(_DELEGATE_MARKER):]

    single = _DELEGATE_TASK_RE.match(raw)
    if single:
        return f"@{single.group(1)} {single.group(2)}"

    parallel = _DELEGATE_PARALLEL_RE.match(raw)
    if parallel:
        try:
            tasks = json.loads(parallel.group(1))
        except (json.JSONDecodeError, TypeError):
            return raw
        if not isinstance(tasks, list) or not all(isinstance(t, dict) for t in tasks):
            return raw
        agents = " ".join(f"@{t.get('agent_name')}" for t in tasks)
        first_task = tasks[0].get("task") if tasks else ""
        if all(t.get("task") == first_task for t in tasks):
            return f"{agents} {first_task or ''}"
        return "; ".join(f"@{t.get('agent_name')}: {t.get('task')}" for t in tasks)
    return raw


def _export_sub_agent(content: str, name: str | None) -> tuple[str, str]:
    """Heading and body for a sub_agent row (content is JSON, see the /report route)."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        data = None
    if not isinstance(data, dict):
        return f"### {name or 'Agent'}", _export_text(content)
    heading = f"### {data.get('display_name') or data.get('agent_name') or name or 'Agent'}"
    meta = []
    for key, unit in (("elapsed", "s"), ("tokens", " tokens"), ("iterations", " steps")):
        if data.get(key) is not None:
            meta.append(f"{data[key]}{unit}")
    if meta:
        heading += f" ({', '.join(meta)})"
    return heading, _export_text(data.get("text") or "")


@router.get("/{session_id}/export")
async def export_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await get_owned_conversation(db, session_id, user)
    # Same branch the chat view shows (see /sessions/{id}/messages); without this
    # a branched conversation exports its pre-branch turns twice.
    branch_id = conv.active_branch or "main"
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.branch_id == branch_id)
        .order_by(Message.sequence, Message.id)
    )
    msgs = result.scalars().all()

    lines = [f"# {conv.title or 'Conversation'}", ""]
    lines.append(f"- Session: `{session_id}`")
    if conv.created_at:
        lines.append(f"- Created: {iso_utc(conv.created_at)}")
    if conv.model:
        lines.append(f"- Model: {conv.model}")
    if conv.agent:
        lines.append(f"- Agent: {conv.agent}")
    lines.append("")

    for m in msgs:
        if m.role in _EXPORT_HEADINGS:
            content = _export_user_text(m.content or "") if m.role == "user" else (m.content or "")
            heading, body = _EXPORT_HEADINGS[m.role], _export_text(content)
        elif m.role == "sub_agent":
            heading, body = _export_sub_agent(m.content or "", m.name)
        else:
            continue
        if not body:
            continue
        lines.extend([heading, "", body, ""])

    lines.extend(["---", "", "*Exported from CrabAgent*", ""])
    return Response(
        content="\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename(conv.title, session_id)}"'},
    )


class AgentSwitchRequest(BaseModel):
    agent: str


@router.post("/{session_id}/agent")
async def switch_agent(
    session_id: str,
    req: AgentSwitchRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = await conv_svc.get_conversation(db, session_id)
    if not conv or conv.user_id != user.id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Session not found")

    await conv_svc.update_conversation(db, session_id, agent=req.agent)

    # NOTE: agent_switch is NOT persisted here anymore — it's deferred to the next
    # user prompt (prompt.py) to avoid interleaving with in-flight tool_calls/tool
    # responses. The prompt handler compares the last active agent from message
    # history with the current effective_agent and only inserts a switch message
    # when the agent actually changed, placing it before the user's new message.

    return {"status": "ok", "session_id": session_id, "agent": req.agent}



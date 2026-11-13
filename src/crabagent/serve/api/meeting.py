from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from crabagent.core.database import (
    MeetingSession,
    User,
    get_db,
    meeting_session_get,
    meeting_session_upsert,
)
from crabagent.serve.deps import get_current_user

router = APIRouter(prefix="/meeting", tags=["meeting"])


class MeetingSessionRequest(BaseModel):
    title: str = ""
    participants: list[str] = []
    notes: list[dict] = []


class BrainstormRequest(BaseModel):
    topic: str = ""
    participants: list[str] = []


def _meeting_to_dict(row) -> dict:
    return {
        "session_id": row.session_id,
        "title": row.title or "",
        "participants": json.loads(row.participants or "[]"),
        "notes": json.loads(row.notes or "[]"),
        "status": row.status or "active",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _owned_or_absent(db: AsyncSession, session_id: str, user_id: int) -> None:
    from sqlalchemy import select

    if await meeting_session_get(db, session_id, user_id) is not None:
        return
    # session_id is globally unique, so a row owned by another user must 404
    # here instead of colliding on insert.
    result = await db.execute(
        select(MeetingSession.id).where(MeetingSession.session_id == session_id)
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=404, detail="Meeting session not found")


async def _session_model_context(db: AsyncSession, session_id: str):
    """Context carrying the session's selected model, so meeting LLM calls use
    the user's actual model instead of a hardcoded default. Returns None if
    the session has no model."""
    from types import SimpleNamespace

    from crabagent.serve.services import conversation as conv_svc

    conv = await conv_svc.get_conversation(db, session_id)
    model = (conv.model or "") if conv else ""
    if not model:
        return None
    return SimpleNamespace(metadata={"model": model})


@router.get("/{session_id}")
async def get_meeting_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await meeting_session_get(db, session_id, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="Meeting session not found")
    return _meeting_to_dict(row)


@router.put("/{session_id}")
async def put_meeting_session(
    session_id: str,
    req: MeetingSessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_or_absent(db, session_id, user.id)
    row = await meeting_session_upsert(
        db,
        session_id,
        user.id,
        title=req.title,
        participants=req.participants,
        notes=req.notes,
        status="active",
    )
    return _meeting_to_dict(row)


@router.post("/{session_id}/end")
async def end_meeting_session(
    session_id: str,
    req: MeetingSessionRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from crabagent.core.meeting import processor

    await _owned_or_absent(db, session_id, user.id)
    row = await meeting_session_get(db, session_id, user.id)
    title = req.title if req else (row.title if row else "")
    participants = req.participants if req else (
        json.loads(row.participants or "[]") if row else []
    )
    stored_notes = req.notes if req else (json.loads(row.notes or "[]") if row else [])

    await meeting_session_upsert(
        db,
        session_id,
        user.id,
        title=title,
        participants=participants,
        notes=stored_notes,
        status="ended",
    )

    notes_text = "\n".join(
        f"[{n.get('timestamp', '')}] {n.get('speaker', '')}: {n.get('content', '')}"
        for n in stored_notes
    )

    result = await processor.process_meeting_notes(
        notes_text,
        meeting_title=title,
        user_id=user.id,
        context=await _session_model_context(db, session_id),
        session_id=session_id,
    )
    return {
        "session_id": session_id,
        "summary": result.get("summary", ""),
        "created_tasks": result.get("created_tasks", []),
        "status": "ended",
    }


@router.post("/{session_id}/brainstorm")
async def brainstorm_meeting_session(
    session_id: str,
    req: BrainstormRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from crabagent.core.meeting import brainstorm as brainstorm_module

    await _owned_or_absent(db, session_id, user.id)
    topic = req.topic if req else ""
    participants = req.participants if req else []
    result = await brainstorm_module.brainstorm(
        topic,
        participants,
        user_id=user.id,
        context=await _session_model_context(db, session_id),
    )
    return {
        "session_id": session_id,
        "ideas": result.get("ideas", []),
        "reactions": result.get("reactions", []),
    }

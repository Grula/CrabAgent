from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from crabagent.core.database import User, get_db
from crabagent.serve.deps import get_current_user

router = APIRouter(prefix="/work/attention", tags=["work-attention"])


@router.get("/summary")
async def attention_summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unified attention summary: what needs the user right now, sorted by priority."""
    from crabagent.core.task.attention import get_attention_summary

    return await get_attention_summary(db, user.id)


@router.get("/by-workspace")
async def attention_by_workspace(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Highest-priority attention state per workspace (switcher badges)."""
    from crabagent.core.task.attention import get_attention_by_workspace

    return {"workspaces": await get_attention_by_workspace(db, user.id)}



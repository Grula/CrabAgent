from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from crabagent.core.database import User
from crabagent.serve.deps import get_current_user

router = APIRouter(prefix="/email", tags=["email"])


class EmailConfigRequest(BaseModel):
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_pass: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    check_interval: int = 300
    enabled: bool = False


@router.get("/config")
async def get_email_config(
    user: User = Depends(get_current_user),
):
    from crabagent.core.mail.config import get_config, _to_dict

    cfg = await get_config(user.id)
    if not cfg:
        return {}
    return _to_dict(cfg)


@router.post("/config")
async def save_email_config(
    req: EmailConfigRequest,
    user: User = Depends(get_current_user),
):
    from crabagent.core.mail.config import save_config as _save
    from crabagent.serve.scheduler import get_scheduler

    cfg = await _save(user.id, req.model_dump(exclude_unset=True))

    # Dynamically update email polling
    sched = get_scheduler()
    if not sched._scheduler.running:
        await sched.start()
    if cfg.enabled and cfg.imap_host:
        sched.start_email_poll_for_user(user.id, cfg.check_interval)
    else:
        sched.stop_email_poll_for_user(user.id)

    from crabagent.core.mail.config import _to_dict

    return _to_dict(cfg)


@router.post("/test")
async def test_email_connection(
    user: User = Depends(get_current_user),
):
    from crabagent.core.mail.config import test_connection

    result = await test_connection(user.id)
    return {"result": result}



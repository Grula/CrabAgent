from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Literal

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from crabagent.serve.deps import get_current_user

router = APIRouter(tags=["collab-browser"])

# Envelope binding required by the collaboration browser bridge (see
# core/computer/bridge_service.py::_handle_http).
PROTOCOL_VERSION = 1
RUNTIME_ID = "browser:collaboration"

# The bridge binds the first task_id it sees and rejects later commands from a
# different one (TASK_MISMATCH). The web viewer is therefore one logical task:
# a single uuid4 generated per process, shared by every proxied command.
WEB_TASK_ID = str(uuid.uuid4())


class NavigateBody(BaseModel):
    url: str


class ActionBody(BaseModel):
    action: Literal["back", "forward", "reload"]


def _bridge_endpoint() -> tuple[str, str]:
    """Return (base_url, token) for the local bridge, or 503 if not running."""
    port = os.environ.get("CRAB_COLLAB_BROWSER_PORT")
    token = os.environ.get("CRAB_COLLAB_BROWSER_TOKEN")
    if not port or not token:
        raise HTTPException(status_code=503, detail="collaboration browser not running")
    return f"http://127.0.0.1:{port}", token


def _envelope(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "runtime_id": RUNTIME_ID,
        # TASK_ID_RE requires exactly 36 hex/dash characters, so the canonical
        # uuid4 string (not .hex) is what the envelope expects.
        "task_id": WEB_TASK_ID,
        "trace_id": str(uuid.uuid4()),
        "command": command,
        "payload": payload,
    }


async def _post_command(session: aiohttp.ClientSession, url: str, token: str, command: str, payload: dict[str, Any]):
    body = _envelope(command, payload)
    async with session.post(url, json=body, headers={"Authorization": f"Bearer {token}"}) as resp:
        return resp.status, await resp.json(content_type=None)


async def _forward_command(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap `command` in the bridge envelope and POST it to the bridge root."""
    base_url, token = _bridge_endpoint()
    url = f"{base_url}/"

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            status, data = await _post_command(session, url, token, command, payload)
            detail = str((data or {}).get("detail") or "")
            # The browser is shared with the agent; if another task owns the
            # binding (or the session is paused), take it over once so the web
            # viewer stays usable.
            if status != 200 and (detail.startswith("TASK_MISMATCH") or detail.startswith("STOPPED")):
                await _post_command(session, url, token, "resume", {})
                status, data = await _post_command(session, url, token, command, payload)
            if status != 200:
                raise HTTPException(status_code=503, detail="collaboration browser unreachable")
            return data
    except HTTPException:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        raise HTTPException(status_code=503, detail="collaboration browser unreachable")


@router.get("/frame")
async def get_collab_browser_frame(user=Depends(get_current_user)):
    base_url, token = _bridge_endpoint()
    headers = {"Authorization": f"Bearer {token}"}

    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base_url}/frame", headers=headers) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=503, detail="collaboration browser unreachable")
                data = await resp.read()
    except HTTPException:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise HTTPException(status_code=503, detail="collaboration browser unreachable")

    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/navigate")
async def collab_browser_navigate(body: NavigateBody, user=Depends(get_current_user)):
    return await _forward_command(
        "navigate", {"url": body.url, "action_id": str(uuid.uuid4())}
    )


@router.post("/action")
async def collab_browser_action(body: ActionBody, user=Depends(get_current_user)):
    return await _forward_command(body.action, {"action_id": str(uuid.uuid4())})

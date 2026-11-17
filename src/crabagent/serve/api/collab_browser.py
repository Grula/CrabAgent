from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Literal

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from crabagent.core.computer.bridge_service import USER_TASK_ID
from crabagent.core.computer.url_policy import local_preview_origin
from crabagent.serve.deps import get_current_user

router = APIRouter(tags=["collab-browser"])

# Envelope binding required by the collaboration browser bridge (see
# core/computer/bridge_service.py::_handle_http).
PROTOCOL_VERSION = 1
RUNTIME_ID = "browser:collaboration"

# The viewer speaks for the signed-in person, not for an agent task. The bridge
# never binds the browser to this id and never refuses it because an agent holds
# the browser, so the viewer has no need to take the browser over (resume).
WEB_TASK_ID = USER_TASK_ID


class NavigateBody(BaseModel):
    url: str


class ActionBody(BaseModel):
    action: Literal["back", "forward", "reload"]


class PauseBody(BaseModel):
    paused: bool


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
            if status == 401 or status >= 500:
                raise HTTPException(status_code=503, detail="collaboration browser unreachable")
            if status != 200:
                # The bridge is up but refused the command (blocked address, page
                # failed to load, ...). Pass its reason on so the viewer can show it.
                reason = detail or "collaboration browser request failed"
                raise HTTPException(status_code=400, detail=reason[:300])
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
                if resp.status == 500:
                    # The browser is running but could not take a picture right now
                    # (usually a page still loading); the viewer keeps its last frame.
                    raise HTTPException(status_code=502, detail="frame capture failed")
                if resp.status != 200:
                    raise HTTPException(status_code=503, detail="collaboration browser unreachable")
                data = await resp.read()
    except HTTPException:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise HTTPException(status_code=503, detail="collaboration browser unreachable")

    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


_LOOPBACK_PREFIXES = ("localhost", "127.0.0.1", "[::1]")


def _viewer_url(raw: str) -> str:
    """Local dev servers speak plain http, so a bare ``localhost:5173`` gets
    ``http://``. Every other bare host keeps the bridge default (https)."""
    value = raw.strip()
    if "://" not in value and value.lower().startswith(_LOOPBACK_PREFIXES):
        return f"http://{value}"
    return value


def _is_local_preview(url: str) -> bool:
    try:
        local_preview_origin(url)
    except ValueError:
        return False
    return True


@router.post("/navigate")
async def collab_browser_navigate(body: NavigateBody, user=Depends(get_current_user)):
    url = _viewer_url(body.url)
    if _is_local_preview(url):
        # The signed-in user typed this loopback address into the viewer, which is
        # the approval the agent's authorize tool asks for. The bridge still grants
        # only this exact origin, for 30 minutes, and refuses CrabAgent's own ports.
        await _forward_command("authorize_local_preview", {"url": url})
    return await _forward_command(
        "navigate", {"url": url, "action_id": str(uuid.uuid4())}
    )


@router.post("/action")
async def collab_browser_action(body: ActionBody, user=Depends(get_current_user)):
    return await _forward_command(body.action, {"action_id": str(uuid.uuid4())})


@router.get("/status")
async def collab_browser_status(user=Depends(get_current_user)):
    data = await _forward_command("status", {})
    return {"paused": bool((data.get("result") or {}).get("paused"))}


@router.post("/pause")
async def collab_browser_pause(body: PauseBody, user=Depends(get_current_user)):
    """Pause or resume the agents. The person at the viewer can still browse."""
    await _forward_command("stop" if body.paused else "resume", {})
    return {"paused": body.paused}

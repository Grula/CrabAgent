"""OfficeCLI installation-status endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from crabagent.core.office.manager import get_office_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/officecli", tags=["officecli"])


@router.get("/status")
async def officecli_status():
    """Return the OfficeCLI install status for the frontend to poll."""
    mgr = get_office_manager()
    status = mgr.get_install_status()
    # Add the available flag so the frontend can decide quickly
    status["available"] = mgr.available
    if mgr.version:
        status["version"] = mgr.version
    return status


@router.get("/perf")
async def officecli_perf():
    """Return lightweight performance stats for recent OfficeCLI commands."""
    mgr = get_office_manager()
    return {
        "available": mgr.available,
        "version": mgr.version,
        "stats": mgr.get_perf_stats(),
    }

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _cli_args(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        query="hi",
        no_persist=True,
        session=None,
        workspace=tmp_path,
        model=None,
        provider=None,
        max_iterations=5,
    )


async def _patch_run_single(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_agent):
    import crabagent.cli.__main__ as cli
    import crabagent.core.database as database

    async def _init_db():
        return None

    async def _ensure_user():
        return SimpleNamespace(id=1)

    async def _provider_ok():
        return True

    async def _setup_agent_context(args, **kwargs):
        return SimpleNamespace(metadata={}, messages=[])

    monkeypatch.setattr(database, "init_db", _init_db)
    monkeypatch.setattr(cli, "_ensure_cli_user", _ensure_user)
    monkeypatch.setattr(cli, "_ensure_provider_configured", _provider_ok)
    monkeypatch.setattr(cli, "_setup_agent_context", _setup_agent_context)
    monkeypatch.setattr(cli, "settings", SimpleNamespace(workspace=tmp_path, skill_discovery_dirs=lambda: []))
    monkeypatch.setattr(cli, "run_agent", run_agent)
    return cli


@pytest.mark.asyncio
async def test_run_single_returns_nonzero_when_the_run_reported_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A provider setup failure returns from the loop; the CLI must still fail."""

    async def setup_failed_run_agent(context, query):
        context.metadata["_run_error"] = True
        return context.messages

    cli = await _patch_run_single(monkeypatch, tmp_path, setup_failed_run_agent)

    assert await cli._run_single(_cli_args(tmp_path)) == 1


@pytest.mark.asyncio
async def test_run_single_returns_zero_on_a_normal_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    async def run_agent(context, query):
        context.metadata["resolved_model"] = "m"
        return context.messages

    cli = await _patch_run_single(monkeypatch, tmp_path, run_agent)

    assert await cli._run_single(_cli_args(tmp_path)) == 0

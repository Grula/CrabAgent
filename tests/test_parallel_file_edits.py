"""Tools in one batch run in parallel threads; edits to one file must not erase each other."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from crabagent.core.agent.tools import edit as edit_tool


@pytest.fixture
def slow_writes(monkeypatch: pytest.MonkeyPatch):
    """Widen the read-modify-write window so a race, if possible, always happens."""
    real_write = Path.write_text

    def slow(self, *args, **kwargs):
        time.sleep(0.05)
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", slow)


async def test_parallel_edits_to_one_file_all_land(tmp_path, slow_writes):
    target = tmp_path / "module.py"
    target.write_text("".join(f"value_{i} = 0\n" for i in range(8)), encoding="utf-8")

    results = await asyncio.gather(*[
        asyncio.to_thread(edit_tool.edit_file, str(target), f"value_{i} = 0", f"value_{i} = {i + 1}")
        for i in range(8)
    ])

    assert all(r.startswith("Successfully") for r in results), results
    assert target.read_text(encoding="utf-8") == "".join(f"value_{i} = {i + 1}\n" for i in range(8))


async def test_an_edit_whose_text_is_already_gone_says_so(tmp_path, slow_writes):
    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")

    results = await asyncio.gather(
        asyncio.to_thread(edit_tool.edit_file, str(target), "x = 1", "x = 2"),
        asyncio.to_thread(edit_tool.edit_file, str(target), "x = 1", "x = 3"),
    )

    assert sorted(r.split(" ")[0] for r in results) == ["Error:", "Successfully"]
    assert target.read_text(encoding="utf-8") in ("x = 2\n", "x = 3\n")

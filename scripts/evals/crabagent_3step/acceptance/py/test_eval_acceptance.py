"""Hidden acceptance tests for crabagent_3step (request.md items 1 and 3)."""

from pathlib import Path

import pytest

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.loop import step_budget_note
from crabagent.core.config import Settings, settings
from crabagent.serve.api import files

PLAN = """# Elite plan

- [x] one
- [X] two
  - [ ] nested open
* [x] star done
* [ ] star open
-[x] not an item (no space)
- [y] not an item
Some text - [x] not at line start

```
- [x] inside a code block
```

- [ ] last
"""


@pytest.mark.asyncio
async def test_plan_entries_carry_checklist_progress(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "elite-plan.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "docs" / "empty-plan.md").write_text("# Empty\n\nno checklist\n", encoding="utf-8")
    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    result = await files.plan_files(workspace=None, user=type("U", (), {"id": 1})())

    by_path = {p["path"]: p for p in result["plans"]}
    assert (by_path["docs/elite-plan.md"]["done"], by_path["docs/elite-plan.md"]["total"]) == (3, 6)
    assert (by_path["docs/empty-plan.md"]["done"], by_path["docs/empty-plan.md"]["total"]) == (0, 0)
    assert by_path["docs/elite-plan.md"]["title"] == "Elite plan"


def _note(step: int, total: int = 80) -> str:
    ctx = AgentContext(workspace=Path("."), max_iterations=total)
    ctx.iteration = step
    ctx.metadata["_sub_agent_depth"] = 1
    return step_budget_note(ctx)


def test_default_window_is_ten():
    assert settings.step_wind_down_steps == 10
    assert _note(70) == "[Step 70 of 80]"
    assert "10 steps left" in _note(71)


def test_window_is_read_when_the_note_is_built(monkeypatch):
    monkeypatch.setattr(settings, "step_wind_down_steps", 5)
    assert _note(75) == "[Step 75 of 80]"
    assert "5 steps left" in _note(76)


def test_zero_turns_the_wind_down_off_but_not_report_now(monkeypatch):
    monkeypatch.setattr(settings, "step_wind_down_steps", 0)
    assert _note(78) == "[Step 78 of 80]"
    assert "make no new edits" in _note(79)


def test_window_needs_a_cap_of_twice_its_size(monkeypatch):
    monkeypatch.setattr(settings, "step_wind_down_steps", 50)
    assert _note(40) == "[Step 40 of 80]"


def test_environment_variable(monkeypatch):
    monkeypatch.setenv("CRAB_STEP_WIND_DOWN_STEPS", "7")
    assert Settings().step_wind_down_steps == 7

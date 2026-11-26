"""Hidden acceptance tests for crabagent_bugfix (backend bugs 3 and 4)."""

import json
import os
import subprocess
import sys
from pathlib import Path

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.loop import step_budget_note


def _note(step: int, total: int = 80) -> str:
    ctx = AgentContext(workspace=Path("."), max_iterations=total)
    ctx.iteration = step
    ctx.metadata["_sub_agent_depth"] = 1
    return step_budget_note(ctx)


def test_wind_down_starts_with_ten_steps_left_counting_the_current_one():
    assert _note(70) == "[Step 70 of 80]"
    assert _note(71).startswith("[Step 71 of 80] 10 steps left")
    assert _note(78).startswith("[Step 78 of 80] 3 steps left")
    assert "make no new edits" in _note(79) and "make no new edits" in _note(80)


_PROBE = """
import json, sys
from pathlib import Path
from crabagent.core.code_index.references import find_references
refs = find_references(Path(sys.argv[1]), "f")
print(json.dumps([[r.kind, r.path, r.line] for r in refs]))
"""


def test_reference_order_does_not_depend_on_the_hash_seed(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "c.py").write_text("from m import f\nf()\nf()\n")
    (tmp_path / "b.py").write_text("from m import f\n\nf()\n")
    (tmp_path / "a.py").write_text("from m import f\nf()\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("from m import f\nf()\n")
    outputs = set()
    for seed in range(12):
        env = {**os.environ, "PYTHONHASHSEED": str(seed)}
        run = subprocess.run([sys.executable, "-c", _PROBE, str(tmp_path)], env=env, capture_output=True,
                             text=True, check=True)
        outputs.add(run.stdout)
    assert len(outputs) == 1, "the order changes with PYTHONHASHSEED"
    calls = [(p, line) for kind, p, line in json.loads(outputs.pop()) if kind == "call"]
    assert calls == [("a.py", 2), ("b.py", 3), ("pkg/c.py", 2), ("pkg/c.py", 3), ("tests/test_a.py", 2)]

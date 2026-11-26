Three changes to CrabAgent. This workspace is a copy of its repository; read AGENTS.md first (tests need `PYTHONPATH=src`).

1. **Plan progress in the API.** Each entry returned by `GET /api/files/plans` (`plan_files` in `src/crabagent/serve/api/files.py`) gets two more integer fields: `total`, the number of markdown checklist items in the file, and `done`, how many of them are checked. A checklist item is a line that, after optional leading spaces, starts with `- [ ]`, `- [x]`, `* [ ]` or `* [x]` (the x may be upper or lower case). Items inside fenced code blocks (between ``` lines) don't count.

2. **Plan progress in the UI.** `PlanFile` in `frontend/src/lib/planContinuation.ts` gets the optional fields `done?: number` and `total?: number`, and `planLabel(plan, max)` appends ` (done/total)` after the name when `total` is greater than 0, e.g. `Elite plan (3/7)`. The `max` limit applies to the name only. Labels of plans without a total stay as they are.

3. **Configurable wind-down.** The step-budget note in `src/crabagent/core/agent/loop.py` starts its "N steps left" wind-down 10 steps before the step cap. Make the window a setting, `step_wind_down_steps` (default 10; `CRAB_STEP_WIND_DOWN_STEPS` in the environment), read each time the note is built. 0 turns the wind-down note off; the "report now" note for the last 2 steps stays. Keep the rule that the wind-down only applies when the cap is at least twice the window.

Add tests for each in the project's existing style, and keep the Python suite, `tsc -b` and vitest passing.

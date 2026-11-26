Bug reports for CrabAgent. This workspace is a copy of its repository; AGENTS.md has the commands (Python tests need `PYTHONPATH=src`).

1. On the start screen, the "Continue a plan" row cuts off a plan whose title is exactly 40 characters long and shows it with "…", although it fits. Longer titles must still be shortened to 40 characters.
2. On the Agents page, typing `200k` into "Compact context above (tokens)" is rejected with "must be at least 20000 tokens".
3. Sub-agents are told "N steps left, finish up" too early, and some Python tests fail.
4. `tests/test_code_references.py::test_same_kind_references_are_listed_by_path_then_line` fails about half the time, locally and in CI; rerunning sometimes makes it pass. Find the cause and fix it in the code (don't change or delete the test), and show that it is no longer flaky.

Fix each bug at its root, with a test that fails without the fix. Keep the Python suite, `tsc -b` and vitest passing, and make the fixes live wherever the project's rules say a change needs it.

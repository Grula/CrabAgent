---
name: proportional-testing
description: Write the smallest set of tests that proves a specific bug fix or micro-feature works (one happy path plus one edge or regression case), in the project's existing test style. Use when verifying an atomic task, to get real coverage without test bloat.
allowed-tools: bash read write edit grep glob
---

# Proportional Testing

Test effort should match the size of the change. A ten-line fix gets one or two focused tests, not a new suite.

## Step 1: Find the target
Read the diff (`git diff`, or the files you just changed). Name the single function, method, endpoint or component whose behavior changed. If the change touches several units, test the one where the behavior lives, not every caller.

## Step 2: Learn the house style
Before writing anything, find out how this project already tests similar code:
- Find the existing tests for that module: `glob` for `test_<module>*`, `*_test.*` or `*.spec.*`, or `grep` for the function name under the test directories.
- Read one or two of them and copy their conventions:
  - framework and file location
  - naming and fixtures
  - async style
  - how they build inputs and mock I/O
- Reuse existing fixtures and helpers instead of writing new ones.
- If no tests exist for this area, follow the nearest test file in the same package, and say so in your report.

## Step 3: Write at most two tests
1. **Happy path:** the intended behavior works for a normal input.
2. **Edge or regression case:** the exact bug that was fixed, or the boundary the feature introduces (empty input, a limit value, an error path).

Guidelines:
- Name each test after the behavior it checks, as `test_<unit>_<scenario>`. Example: `test_parse_price_rejects_negative_amounts`.
- Use arrange, act, assert, with one behavior per test.
- Assert on observable results (return values, state, output), not on private details.
- Mock only what the test can't control (network, clock, randomness, external services). Never mock the unit under test.
- Keep tests deterministic: no sleeps, no real network, no dependence on test order.
- Add the tests to an existing test file when one covers this unit, using `edit`. Create a new file only if
  none does.
- If you don't have `edit`, put the tests in a new file. Never rewrite an existing test file with `write`
  just to add tests: one mistake deletes the tests that were already there.

## Step 4: Prove the tests matter
For a bug fix, the regression test should fail without the fix. If it's cheap:
1. Temporarily revert the fix. If reverting isn't practical, reason it through from the diff instead.
2. Confirm the test fails.
3. Restore the fix and confirm the test passes.

A test that passes either way proves nothing. Rewrite it.

## Step 5: Run and report
- Run only the new tests first, for example `pytest path/to/test_file.py::test_name -q`.
- Then run the whole file they live in, to catch interference between fixtures.
- Report:
  - **PASS** or **FAIL**
  - the tests you added (`file::name`) and what each one proves
  - the exact command you ran and its result
  - for a FAIL, the failing assertion and whether the bug is in the code or in the test

## Out of scope
Don't write tests for:
- unrelated modules
- full-system integration, unless the task was integration work
- performance
- "while I'm here" coverage

Don't refactor existing tests.

## When the code under test doesn't exist yet (TDD RED)

A suite that only fails at collection (ModuleNotFoundError/ImportError, exit code 2) never executes its test bodies, so it proves nothing about your assertions. Before declaring RED:

1. Create a throwaway stub so the module imports (e.g., sys.modules['slugify'] = types.ModuleType('slugify')).
2. Run the suite and confirm each test fails on its assertion, not on import.
3. Delete the stub and report the collection failure.

Never claim a test 'will fail correctly' from a syntax check alone.

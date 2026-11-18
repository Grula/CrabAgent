# Workflow patterns for CrabAgent

Each pattern names its shape, when it fits, and the calls to make. Agent names are
the default team; use the matching agent from your Agent Team list if yours differ.

## Test first, then implement, then review
Fits new behaviour, behaviour changes and bug fixes.
```json
run_pipeline({"steps": [
  {"id": "test-lockout", "agent_name": "tester", "depends_on": [],
   "task": "Write failing tests for: <behaviour>. Files: tests/test_auth.py. Run them and confirm each fails on its assertion. Reply: STATUS, TESTS: <file::name>, RED: <command> -> <key line>."},
  {"id": "impl-lockout", "agent_name": "coder", "depends_on": ["test-lockout"],
   "task": "Make the failing tests in tests/test_auth.py pass by changing src/auth.py only. Do not edit the tests. Reply: STATUS, CHANGED, CHECK: <command> -> <result>."},
  {"id": "review-lockout", "agent_name": "the_reviewer", "depends_on": ["impl-lockout"],
   "task": "Review the uncommitted change to src/auth.py and tests/test_auth.py against this task: <task>. Reply with the strict-review verdict format."}
]})
```
If the review fails, send its findings to one corrective `coder` task, then
review again. Stop after 2 rounds and report.

## Fan-out, then you merge
Fits independent lookups, where you need every reply in full.
```json
delegate_parallel({"tasks": [
  {"agent_name": "researcher", "task": "<question A brief>"},
  {"agent_name": "researcher", "task": "<question B brief>"}
]})
```
Merge the results yourself. Prefer this to a pipeline merge step whenever the outputs
are long, because a pipeline step sees only 1500 characters of each.

## Independent changes in parallel
Fits several changes that touch different files.
```json
delegate_parallel({"tasks": [
  {"agent_name": "coder", "task": "<change 1: files under src/api/ only>"},
  {"agent_name": "coder", "task": "<change 2: files under src/ui/ only>"}
]})
```
Then one `the_reviewer` task over the combined diff, listing which files belong to
which change.

## Per-item batch in waves
Fits the same operation on many items: files, endpoints, records.
1. Normalise and dedupe the item list. Write one brief template with a fixed reply
   format.
2. Run wave 1 with 4-8 items through `delegate_parallel`. Check the replies against
   the format and fix the template if needed.
3. Run the remaining waves and collect the results into a table.
4. Retry failed items once. Report `success/total` and list the gaps.

Parallel items must never touch the same file.

## Plan, critique, execute
Fits large or risky changes.
1. Draft the plan yourself (see the `plan` skill): files, steps, checks.
2. `delegate_task` to `the_reviewer`: "Read the files this plan touches. Do not edit.
   List what would break, what is missing, and any wrong assumptions."
3. Revise the plan, then run it as a pipeline or in waves.

## Anti-patterns
| Mistake | Consequence |
|---|---|
| `delegate_task` in sequence for independent work | Takes N times as long. Use `delegate_parallel` |
| A pipeline step that needs long upstream output | It sees 1500 characters of each. Merge yourself, or pass files |
| Raw data in `shared_put` | Bloats every later sub-agent's prompt |
| Two parallel steps editing one file | Conflicting writes |
| Web work given to `coder`, or edits given to `researcher`, `scout` or `the_reviewer` | The step fails on missing permissions |
| A checker asked "is it good?" | Rubber stamp. Ask for defects instead |
| Unbounded retries | Burned budget. Retry once, then report the gap |

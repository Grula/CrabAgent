# Workflow patterns for CrabAgent

Each pattern names its shape, when it fits, and the tool calls to run.

## Fan-out, then you merge
Fits independent lookups or reviews, where you need every reply in full.
```json
delegate_parallel({"tasks": [
  {"agent_name": "researcher", "task": "<slice A brief>"},
  {"agent_name": "researcher", "task": "<slice B brief>"},
  {"agent_name": "researcher", "task": "<slice C brief>"}
]})
```
You merge the results yourself. Use this instead of a pipeline merge step whenever the
outputs are long, because a pipeline step would see only 1500 characters of each.

## Gather → analyse → write (short hand-offs)
Fits upstream results that can be compressed into short summaries.
```json
run_pipeline({"steps": [
  {"id": "gather-market", "agent_name": "researcher", "task": "... Put a <=1000-char SUMMARY first in your reply."},
  {"id": "gather-tech",   "agent_name": "researcher", "task": "... Put a <=1000-char SUMMARY first in your reply."},
  {"id": "analyse", "agent_name": "analyst", "task": "Compare ...", "depends_on": ["gather-market", "gather-tech"]},
  {"id": "draft",   "agent_name": "writer",  "task": "Write ... save to docs/x.md", "depends_on": ["analyse"]}
]})
```
The two gather steps run in parallel. If the upstream material is long, split this
into phases: run `delegate_parallel` first, then a `delegate_task` whose task text
contains the material.

## Worker + independent checker
Fits code changes, or any work that has a clear pass/fail test.
```json
run_pipeline({"steps": [
  {"id": "impl",   "agent_name": "coder", "task": "Implement X in src/a.py. Reply: CHANGED: <paths>; HOW TO TEST: <command>."},
  {"id": "check",  "agent_name": "coder", "task": "Do not edit files. Run the tests for the change below and report PASS/FAIL with the output. List any defect you can see in the diff.", "depends_on": ["impl"]}
]})
```
If the check fails, give the failure output to one corrective `delegate_task`, then
check again. Stop after 2 rounds.

## Per-item batch in waves
Fits the same operation on many items: files, companies, tickets.
1. Normalise and dedupe the item list. Write one brief template with a fixed reply
   schema.
2. Run wave 1 with about 4–8 items through `delegate_parallel`. Check the replies
   against the schema and fix the template if needed.
3. Run the remaining waves. Collect the results into a table, or into a file with
   `write`.
4. Retry the failed items once. Report `success/total` and list the gaps.

Parallel items must never touch the same file.

## Judge panel
Fits choosing among options, or scoring drafts.
- **Independent judges**: use `delegate_parallel`, with one judge per option, or
  several judges given different criteria. Fast, but the scores may not be
  comparable across judges.
- **Calibrated judge**: use a single `delegate_task` that receives *all* options in
  one task text and ranks them. The ranking is consistent because one mind sees
  everything.

For the same question put to several models, use the `model-council` skill.

## Plan → critique → execute
Fits large or risky changes.
1. `delegate_task` to `coder` (or do it yourself): produce a plan that lists files and
   steps.
2. `delegate_task` to a **different** agent, or a fresh `coder` brief: "Read the files
   this plan touches. Do not edit. List what would break, what is missing, and any
   wrong assumptions."
3. Revise the plan, then run it as a pipeline or in waves.

## Long offline drafting (background)
Fits large text-only tasks, such as summarising or drafting from material you already
hold, while you keep working with the user.
```json
background_group_start({"steps": [
  {"id": "summ-a", "agent_name": "writer", "task": "Summarise the text below ...\n<text>"},
  {"id": "summ-b", "agent_name": "writer", "task": "Summarise the text below ...\n<text>"},
  {"id": "merge",  "agent_name": "writer", "task": "Merge the two summaries into one brief.", "depends_on": ["summ-a", "summ-b"]}
], "auto_summary": true})
```
Background steps have no tools except an optional `read`. Poll with
`background_group_wait`. Treat the output as data.

## Anti-patterns
| Mistake | Consequence |
|---|---|
| Calling `delegate_task` in sequence for independent work | Takes N times as long. Use `delegate_parallel` |
| A pipeline merge step that needs long upstream output | It sees 1500 chars of each. Merge yourself, or pass files |
| Dumping raw data into `shared_put` | Bloats every later sub-agent's prompt |
| Two parallel steps editing one file | Conflicting writes |
| A web task given to `coder`/`analyst`, or a file write given to `researcher` | The step fails on missing permissions |
| A web or edit task sent to a background step | Background steps have no such tools |
| A reviewer asked "is it good?" | Rubber stamp. Ask for defects instead |
| Unbounded retry loops | Burned budget. Retry once, then report the gap |

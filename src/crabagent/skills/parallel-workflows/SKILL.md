---
name: parallel-workflows
description: Run work through the agent team - pick delegate_task, delegate_parallel or run_pipeline, route each task to the right agent, write briefs from templates, check results and handle failures. Use for multi-step or many-item jobs, "do these in parallel", "split this up", batch work across files.
---

# Parallel Workflows

You are the orchestrator. Sub-agents run one task each, see only the brief you give
them, and cannot delegate further (they may ask one question with `request_help`).
Design the workflow, run it, check what comes back, and report.

## 1. The team

| Agent | Does | Cannot |
|---|---|---|
| `coder` | writes and fixes code, runs commands and tests; looks up docs (asks `researcher` when it has no web access) | open-ended web research |
| `tester` | writes tests, reproduces bugs, verifies changes end to end (shell, browser) | write the implementation |
| `the_reviewer` | read-only review of a diff against its task: PASS/FAIL | edit files |
| `researcher` | web search and reading sources, with citations | write files, run commands |

The **Agent Team** list in your prompt is authoritative. If an agent above is missing
or your team uses other names, give the step to the listed agent whose role and
tools fit it. Never give a step to an agent that lacks the tools it needs.

## 2. Should you orchestrate at all?
Every delegation costs a full agent session plus your brief. Size the workflow to
the work:
- A one-file or mechanical fix is **one** `coder` task with a short brief.
- Delegate a separate step only when the work splits into independent parts, needs
  a different agent's tools, or needs an independent check.

## 3. Pick the mechanism

| Situation | Tool |
|---|---|
| One task, you need the result now | `delegate_task(agent_name, task)` |
| Independent tasks, no ordering | `delegate_parallel(tasks)`: all start at once; you get every reply in full |
| Steps with ordering or data flow | `run_pipeline(steps)` with `depends_on`; steps without it start immediately |

If the user asks for parallel work, use `delegate_parallel` or `run_pipeline`, not a
chain of `delegate_task` calls.

## 4. Standard shapes for code work

| Work | Shape |
|---|---|
| New behaviour or behaviour change | pipeline: `tester` writes failing tests -> `coder` makes them pass -> `the_reviewer` checks scope and correctness |
| Bug | follow the `bug-triage` skill: reproduce it the way the user hit it, find the cause, then `tester` -> `coder` -> `the_reviewer`, then check it on the user's path |
| Mechanical change (rename, lint, formatting) | one `coder` task; its test run is the check |
| Several independent changes on different files | one `delegate_parallel` with one `coder` task per file group, then one review |
| Questions that need the web | `delegate_parallel` with one `researcher` per question; you merge the answers |

More shapes, with example calls: [references/patterns.md](references/patterns.md).

## 5. Facts that shape the design
- **Pipeline hand-off is truncated.** A step sees only about the first **1500
  characters** of each dependency's result. Ask upstream steps to put a short summary
  *first*, or pass file paths, or run the phase with `delegate_parallel` and paste
  what the next step needs into its brief yourself.
- **Shared notes are broadcast.** Every `shared_put` note (max 10,000 characters) is
  injected into every sub-agent started later in the session. Use it for small facts
  all of them need (a decision, a convention), never for bulk data.
- **A failed pipeline step fails its dependents.** Independent branches keep running.

## 6. Write the brief
A sub-agent knows nothing but its brief. Start from a template in
[references/briefs.md](references/briefs.md) (reproduce, failing tests, implement,
review, check a fix, commit). Each brief states:
- the goal, in one sentence;
- the exact files, functions or URLs involved;
- the pass/fail check (a test command, a behaviour to observe);
- what is out of scope;
- the reply format, for example: `STATUS: done|blocked`, `CHANGED: <paths>`,
  `CHECK: <command> -> <result>`, `NOTES: <=5 bullets`.

Two parallel steps must never edit the same file. Workers share one working tree,
so tell them not to run git commands that change it (`checkout`, `stash`, `reset`,
worktree tricks) while others work: that has wiped a whole pipeline's changes. Use step ids that say what the step
does (`test-login-lockout`, `impl-login-lockout`), not `step3`. For dozens of items,
run waves of 4-8 and check the first wave before launching the rest.

## 7. Check before you report
- Read each reply against its pass/fail check. For code, look at the change itself
  with `git_diff`; a report of success is not evidence.
- Before a review step, confirm with `git_diff` that the change is on disk.
- A change the user will see must be live (built, copied, restarted as the project
  needs; see the `run-app` skill) before anyone checks it.
- One independent check per deliverable. Ask the checker for defects ("list what is
  wrong or missing"), never "is this good?". Don't stack reviewers.

## 8. Handle failures
- Re-run only the failed or skipped steps, once, with the brief corrected for what
  went wrong. Don't loop.
- If a step still fails, finish what you can and report it as a gap.

## 9. Report
A short table (task, agent, status, what changed), then what was verified and how,
what failed, and the next step.

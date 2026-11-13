---
name: verify
description: Prove a code change works by running the real program and observing the changed behavior at its actual surface (CLI, HTTP API, web UI, library import), probing edge cases, and leaving a focused regression test for bug fixes. Use before claiming a nontrivial change is done, or when asked "does it work", "test this end to end", "verify the fix".
---

# Verify a Change End to End

A change is verified when you have **watched the running program do the new thing**
through the interface its users touch, and captured the output as evidence. Green tests,
a clean build, or code that "looks right" are supporting signals, not proof.

Two outcomes matter:
1. **Runtime evidence** - you drove the changed path and saw the expected result.
2. **A durable check** - for a bug fix or behavior change, a small maintained test now
   guards it so it does not silently break again.

Skip this skill when the change has no runtime surface (docs only, comments, type
annotations with no runtime effect, test-only edits). Report `SKIP` with one line why.

## 1. Know exactly what changed

```bash
git status --short
base=$(git merge-base HEAD @{upstream} 2>/dev/null || git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main)
git log --oneline "$base"..HEAD
git diff --stat "$base"
```

Read the diff, then state the **claim** in one sentence: what should now happen that did
not before (or should no longer happen). If the user or commit message describes the
change differently from what the diff does, that mismatch is itself a finding.

## 2. Find the surface

Internal functions are not surfaces. Follow the call chain outward until it reaches
something a user or another program touches, and verify there.

| Change reaches | Surface | How to drive it |
|---|---|---|
| command-line tool | terminal | run the command with `bash`, capture stdout/stderr/exit code |
| server / API | network socket | start it with `bash` (`background: true`), poll until ready, send requests with `curl`, read its log with `bash_output` |
| web front end | browser | `browser_navigate`, `browser_click`, `browser_type`, `browser_screenshot`; use `collab_browser_*` when the user should watch |
| desktop GUI | screen | `computer_observe` / `computer_act` |
| library | public import | a scratch script that imports the installed package the way a consumer would - not deep `src/` paths |
| scheduled / background job | its trigger | invoke the job entry point once against safe data |
| agent prompt, skill, or tool | the agent | run the agent on a task that exercises it and read what it did |

Worked examples: [references/surfaces.md](references/surfaces.md).

## 3. Get a handle on running it

Look for an existing recipe before improvising: AGENTS.md, README, Makefile/justfile,
`package.json` scripts, `pyproject.toml` scripts, docker-compose files, CI workflows, and
workspace skills in `.crabagent/skills/`. Use the project's own environment (see
`python-env` for Python projects).

If there is no recipe, cold-start it from those files, timeboxed. If you cannot get the
program to a state where the change is observable, stop and report `BLOCKED` with the
exact command and error where it stopped.

When a cold start took real effort, offer to save the working recipe (build, launch,
how to reach the changed flow, gotchas) as a short workspace skill at
`.crabagent/skills/run-app/SKILL.md` so the next session starts fast.

## 4. Drive the changed path

Plan the smallest sequence that makes the changed code execute:

- New flag -> run with it. Changed endpoint -> call that endpoint.
- Changed error handling -> trigger that error.
- Changed rendering -> load the page and take a screenshot.

**Read the plan back before executing.** If every step is "build", "type-check", or "run
the test suite", you have planned a CI run, not a verification. Add a step that reaches
the surface, or report `BLOCKED`.

Also confirm the old, unchanged behavior next to it still works (one non-regression step).

**Destructive paths** (deleting data, sending email, publishing, payments, writing outside
the workspace): never run them live. Use a dry-run flag, a temp directory, a local mock,
or a test account. If none exists, verify around it and state what was not exercised.

**Isolation:** use a free port, a `mktemp -d` directory, and stop any process you started
(`bash_stop` with the PID that `bash` reported) before you finish.

## 5. Push on it

Confirming the happy path is half the job. Try at least one probe the author probably did
not, chosen from what the change touches:

- new option: empty value, repeated, combined with a conflicting option, misspelled
- new endpoint: wrong method, missing field, malformed or oversized body, no auth
- changed error path: the neighbouring errors - are they still handled?
- state/persistence: do it twice, with stale data, from two sessions at once
- UI: double-click, back button, reload mid-flow, narrow viewport

A probe that holds is still worth reporting ("empty `--name` -> clear error, exit 2").

## 6. Leave a durable check (bug fixes and behavior changes)

- Add or update the **smallest** test in the project's existing framework and layout that
  exercises the changed behavior. A scratch script or manual probe does not replace it.
- When safe and runnable, watch the test **fail before** the fix and **pass after** it.
- Never weaken, skip, or delete a real failing test to get green. Fix the code, or explain
  why the test's expectation is wrong before changing it.
- Don't introduce a new test framework just for this. If the project has no test harness,
  say that durable coverage was not added and why.
- Run the focused test after your final edit. Run the full suite only when the project
  requires it or the risk justifies it.
- If the test runner cannot run here, still leave the test and say it was not executed.
  Never report a result you did not observe.

Skip this section for pure refactors, verification-only requests, or when the user declines tests.

## 7. Report

```
## Verification: <one-line summary of the change>

Verdict: PASS | FAIL | BLOCKED | SKIP
Claim: <what the change should do; note any mismatch with the diff>
Method: <how you launched it - recipe used or cold start>

Steps
1. [ok]    <action on the running program> -> <observed result>
           <captured output: response body, terminal output, screenshot path>
2. [probe] <edge case tried> -> <result>
3. [fail]  ...

Regression test: <file::test name - failed before / passes after | not added: reason>

Findings
- [warn] <anything surprising, slow, confusing, or broken - even if unrelated>
- <probes that held>
```

Verdicts:
- **PASS** - you ran it and the change behaves as claimed at its surface.
- **FAIL** - it does not, it breaks something else, or claim and diff materially disagree.
  "3 of 4 worked" is FAIL.
- **BLOCKED** - you could not reach a state where the change is observable. Say exactly where it stopped.
- **SKIP** - no runtime surface exists.

When the evidence is ambiguous, call it FAIL and attach the raw output. A false PASS ships
broken code; a false FAIL costs one more look.

<!-- Section 6 adapted from Meta muse-code skills/durable-test-collateral (Apache-2.0). Modified for CrabAgent. -->

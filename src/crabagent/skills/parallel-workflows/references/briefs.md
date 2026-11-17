# Brief templates

Copy one, fill in every `<...>`, and delete lines that don't apply. Each brief keeps
the worker inside a fixed scope and tells it when to stop. Vague briefs are the main
cause of long, expensive sub-agent runs.

Rules for every brief:
- Name the files. "Read only these unless one is missing something you need" saves
  more tokens than anything else.
- Give the exact command that decides pass or fail, and say "run only this".
- Paste the facts you already established (root cause, decisions, file:line). The
  worker must not rediscover them.
- End with the reply format, so you can check the result quickly.

## Reproduce a reported bug (tester)
```
Reproduce this bug exactly the way the user hit it. Do not fix anything.
User's path: <page and control / command / API call, with the exact input>
Observed: <what they saw, quoted>    Expected: <what should happen>
Their evidence: <error, log lines, screenshot description>
How to run the app: <recipe, or "see the run-app skill">
Do: drive that path in the running program and capture the failing output.
If it does not fail, find what differs from the user's setup and report it.
Stop when you have the failing output, or after <N> attempts.
Reply: STATUS: reproduced | not reproduced
       STEPS: <exact steps/commands>
       OUTPUT: <the failing output, trimmed to what matters>
       DIFFERS: <only if not reproduced>
```

## Failing tests for a behaviour (tester)
```
Write the smallest set of tests that fails because <behaviour> is missing or wrong.
Behaviour: <rule 1>; <rule 2>; <edge case that matters>
Root cause (bugs only): <one sentence, file:line>
Test file: <path> (follow the style of <nearby test file>)
Don't: write the implementation, or touch files other than the test file.
Run only: <command for these tests>, and check that each one fails on its assertion,
not on import.
Stop when they fail for the stated reason.
Reply: STATUS: red | blocked
       TESTS: <file::name, what each proves>
       RED: <command> -> <key failing line per test>
```

## Implement or fix (coder)
```
Make <tests / behaviour> pass by changing <files> only.
Context: <root cause or design decision, file:line, signatures to keep>
Don't: edit the tests (mechanical import or syntax fixes are fine), refactor
unrelated code, add options nobody asked for, or touch project-wide config.
Check: <exact test command>; run only this, plus <nearby file's tests> at the end.
Make it live if the project needs that: <build/copy step, or "see run-app">
Stop when the check passes.
Reply: STATUS: done | blocked
       CHANGED: <paths>
       CHECK: <command> -> <result line>
       NOTES: <=3 bullets (anything the reviewer should look at)
```

## Review a change (the_reviewer)
Before you send this, confirm with `git_diff` that the change exists. Sending a
review of work that isn't on disk wastes a whole run.
```
Review the uncommitted change to <files> against this task: <task text>.
Only these files belong to the task; ignore other changes in the working tree.
Check: is anything outside the task, is any part of it missing, and is there a
correctness bug you can show with a concrete trigger?
Don't: edit files or propose improvements beyond the task.
Reply: VERDICT: PASS | FAIL
       Out of scope / Missing / Bugs: <file:line - one-line reason each>
```

## Check a fix where the user hits it (tester)
```
Verify that <fix> works on the user's path, in the running program.
The change is live: <what was rebuilt/copied/restarted, or how to start a preview>
Repeat exactly: <the reproduction steps from the bug report>
Also check: <one neighbouring behaviour that must still work>
Don't: change code. Report what you saw, with output or screenshots.
Reply: VERDICT: PASS | FAIL | BLOCKED
       STEPS: <step -> observed result>
       EVIDENCE: <output, screenshot path>
```

## Commit (coder)
Only when the user asked for a commit.
```
Commit <task> as <one | N> commit(s), following the atomic-commit skill.
Files per commit: <commit 1: paths, message idea>; <commit 2: ...>
Leave every other change in the working tree untouched. Don't push.
Reply: COMMITS: <hash - summary - files>
       LEFT UNCOMMITTED: <paths>
```

---
name: strict-review
description: Audit a diff against the task it was meant to solve and flag scope creep, YAGNI violations and over-engineering, ending in a PASS/FAIL verdict. Use when reviewing a completed atomic task before it is committed; for correctness bugs use code-review instead.
allowed-tools: git_diff read grep glob bash
---

# Strict Scope Review

You are checking one thing: does this diff do the task, the whole task, and nothing but the task? You are not reviewing style, design taste or correctness; the `code-review` skill covers correctness. Put any bugs you happen to notice in a separate note, not in the verdict.

## Inputs
- **The task:** its description, acceptance criteria and ID. Scope can't be judged without it: if you can't find it, ask, or if you were delegated this review and cannot ask, report that it cannot be done.
- **The change:** what was added, modified or deleted for this task. Get the real diff with `git_diff`,
  which is read-only:

  | What to review | Call |
  |---|---|
  | Uncommitted changes (the usual case) | `git_diff` with no arguments |
  | A branch against its base | `git_diff` with `base: "main...HEAD"` |
  | One commit | `git_diff` with `commit: "<sha>"` |
  | A large change | `stat_only: true` first, then one call per file with `paths` |

  The result also lists new untracked files. They are not in the diff, so `read` each one in full. Other
  agents or the user may have uncommitted work in the same repository: review only the files that belong
  to this task (the task or the implementer's report names them) and ignore the rest.

  If `git_diff` is unavailable or the workspace is not a Git repository, work from the diff or the list of
  changed files given in your task and `read` those files in full. You then see what the code is now but
  not what it was before, so flag code as out of scope only when it clearly belongs to this change, and say
  in your verdict that you reviewed file contents rather than a diff. With neither a diff nor a file list,
  stop and report that the review cannot be done.

## Step 1: Define the minimum
In one sentence, state the smallest change that would satisfy the task. Then list what that change necessarily includes:
- the code change itself
- the tests that prove it
- the imports, wiring, migrations or docs it cannot work without

This list is your yardstick.

## Step 2: Audit every hunk
Put each changed hunk into one of three groups:
- **Required:** it is on the minimum list.
- **Supporting:** a required change needs it to compile, run or be tested.
- **Out of scope:** anything else, including:
  1. New functions, classes, parameters, config options, files or dependencies the task doesn't need.
  2. Refactors, renames, reformatting or "while I was here" fixes to code the task didn't have to touch.
  3. Changes to public interfaces (signatures, API responses, CLI flags, schemas) the task didn't ask for.
  4. Error handling, validation, fallbacks or abstractions for situations that neither the task nor the code's real callers can produce.
  5. Speculative generality: options, hooks or layers added "for later".
  6. Leftovers: debug output, commented-out code, TODOs, unused imports.

Also check the other direction: is any part of the task **missing** from the diff? An incomplete diff fails too.

## Step 3: Verdict
Use exactly this format, and omit empty sections:

```
VERDICT: PASS | FAIL
Minimum change: <one sentence>

Out of scope (delete or move to a separate task):
- path/to/file.py:42-58: <what it is and why it isn't required>

Missing from the task:
- <requirement not implemented>

Notes (not part of the verdict):
- <bugs or risks you noticed, if any>
```

- **PASS** only when nothing is out of scope and nothing is missing.
- **FAIL** lists every offending hunk by file and line range, each with a one-line reason.

Don't propose improvements, alternative designs or extra tests. Your job is to enforce scope, not to expand it.

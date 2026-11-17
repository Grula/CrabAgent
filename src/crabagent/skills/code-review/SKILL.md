---
name: code-review
description: Hunt for correctness bugs in a diff (uncommitted work, a branch vs its base, a commit, or a path) and report severity-ranked findings, each with a concrete failure scenario checked against the code. Use for "review my changes", "check this branch/PR for bugs", "is anything wrong with this diff".
---

# Code Review (correctness)

Goal: find the defects that would make the changed code misbehave, prove each one
against the actual source, and hand back a short ranked list the author can act on.

This skill is about **correctness**. Related skills cover other concerns:
`strict-review` (scope creep / YAGNI), `simplify` (reuse and cleanup, applies edits),
`security-review` (exploitable vulnerabilities).

The review is **read-only**. Do not edit, stage, or commit anything unless the user
asks you to fix the findings afterwards.

## 1. Pin down what is under review

Pick the target from the request:

| Request | What to diff |
|---|---|
| nothing specific | everything on the branch since its base **plus** uncommitted work |
| a commit sha | `git show <sha>` |
| a branch name | merge base of HEAD and that branch, then `git diff <base>` |
| a file or directory | `git diff HEAD -- <path>`; read the whole file if it is new or there is no repo |

Default commands:

```bash
git status --short
base=$(git merge-base HEAD @{upstream} 2>/dev/null || git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main)
git log --oneline "$base"..HEAD
git diff --stat "$base"
git diff "$base"                              # committed + working tree vs. merge base
git ls-files --others --exclude-standard      # untracked files never show in a diff: read them whole
```

- No `bash`? The read-only `git_diff` tool gives the same information: no arguments for
  uncommitted work, `base: "main...HEAD"` for a branch, `commit` for one commit.
- A large diff: redirect it to a file and `read` it in chunks (with `git_diff`, use
  `stat_only` and then `paths`). Never review a truncated diff.
- Read the project rules that govern the touched code (AGENTS.md and any docs it points to).
  A clear violation of a written rule counts as a finding when you can quote the rule.
- If the user, commit message, or PR text says what the change is supposed to do, keep that
  claim at hand. Code that does something different from its stated intent is a finding.

## 2. Choose a depth

- **Quick** - user asks for a quick look, or the diff is a few dozen lines. One careful
  pass over the hunks (pass A below), cap 5 findings.
- **Standard** (default) - every pass in section 3, verify everything, cap ~10 findings.
- **Deep** - user asks for a thorough review, or the diff is large/risky (auth, data
  migrations, concurrency, money). All passes, the gap sweep, cap ~15 findings.

## 3. Collect candidates, one lens at a time

Run each pass on its own and write candidates down as you go: `file:line`, a one-line
claim, and *trigger -> consequence*. A later pass must not talk you out of an earlier
candidate; verification (section 4) is the only filter. Dropping half-believed ideas
too early is the most common way real bugs slip through.

**A. Hunk walk.** For every hunk, read the entire enclosing function, not just the
changed lines - an untouched line inside a touched function is in scope. For each
changed line ask which input, state, ordering, or platform breaks it: inverted or
widened condition, boundary off by one, value that can be None/null/undefined,
truthiness test that misfires on `0`/`""`/empty collection, missing `await`, wrong
variable after copy-paste, exception swallowed or re-raised as the wrong type,
resource left open on the error path, unescaped string in a regex/SQL/shell/HTML context.

**B. What got removed.** For every deleted or rewritten line, name the guarantee it
used to provide (a guard, a validation, an error path, a cleanup, a test case). Then
find where the new code provides it. Nowhere = candidate.

**C. Callers and callees.** `grep` for each changed function, method, class, constant,
config key, env var, route, DB column, or serialized field. Check every call site
against the new signature, return shape, raised exceptions, side effects, and timing.
For renamed or reshaped data, look for producers and consumers that still use the old form.

**D. Language and framework traps.** Check the diff against
[references/language-pitfalls.md](references/language-pitfalls.md) for the languages involved.

**E. State and concurrency.** Shared mutable state, cache invalidation, check-then-act
races, a lock whose scope shrank, async tasks cancelled mid-update, retries around
non-idempotent work, partial writes without a transaction, setup/teardown asymmetry.

**F. Wrappers and adapters.** A new type that wraps another (cache, proxy, decorator,
adapter) must forward every method callers use and call the *wrapped* instance - not
route back through a registry or global, which recurses or bypasses itself.

**G. Tests in the diff.** Does each new test actually exercise the new behavior and
fail without it? Tests that mock the unit under test, assert nothing meaningful, or
were weakened/deleted to get green are findings.

**Gap sweep (Deep only).** With the list in hand, re-read the diff looking only for
what is *not* on it yet: guards lost when code was moved or extracted, defaults that
flipped, mutable defaults evaluated once, ordering that depends on hash/dict/set
iteration, predicates with side effects, error messages that now lie.

**Splitting work on a big diff (Deep).** You may hand passes to read-only sub-agents
with `delegate_parallel` using the read-only `the_reviewer` agent (or another read-only
agent from your team list). Each task must stand alone: the
repo path, the exact `git diff` command, the file list, ONE pass description copied
from above, and the required output (`file:line | claim | trigger -> consequence`).
Describe the lens, not your suspicion, so the sub-agent is not steered toward
confirming it. Verification stays with you.

## 4. Verify every candidate before reporting

Try to *refute* each one:

1. Re-open the cited file at the cited line. Work from the code, not your memory of the diff.
2. Trace the trigger backwards. Can the bad input or state actually reach this line, or
   is it rejected upstream, excluded by types, or never produced by any caller?
3. When it is cheap and safe, demonstrate it: run the relevant existing test, or a tiny
   throwaway script in a scratch directory that feeds the triggering input. Never edit
   repo files to prove a point.

Label the outcome:

- **confirmed** - you can state the trigger and the wrong result and point at the line.
- **likely** - the mechanism is real but the trigger depends on timing, environment, or
  config you could not observe. Say what would settle it.
- **refuted** - guarded elsewhere or misread. Drop it.

Report confirmed findings; report likely ones only when the impact would be high.
Merge candidates that share one root cause.

## 5. Rank

- **P0** - data loss or corruption, security hole, crash or hang on a main path, broken build.
- **P1** - wrong result on realistic input, broken caller, regression of existing behavior.
- **P2** - edge-case bug, mishandled error path, leak under specific conditions.
- **P3** - unlikely edge case, misleading message, test that cannot fail.

Only report problems the change introduces or re-exposes. A severe pre-existing bug
you happened to see may get one line under "Outside this diff".

## 6. Report

```
## Code review: <target> - <N files, +added/-removed>, <depth>

1. [P1] <imperative title> - path/to/file.py:123 (confirmed)
   Scenario: <concrete trigger> -> <wrong outcome>. <short reason, quote the line if short>
   Fix: <one-line direction>

2. ...

Checked: <passes run, what was verified by execution>
Residual risk: <untested paths, things you could not run>
Outside this diff: <optional>
```

- Most severe first. If more survive than the cap, keep the most severe and say how many were cut.
- Nothing found: say "No correctness issues found", list what you checked, and note residual
  risk. Never invent a finding to fill the list.

## If asked to fix

Fix one finding at a time with the smallest change, rerun the check that demonstrated
it, then use the `verify` skill to confirm the whole change still works end to end.

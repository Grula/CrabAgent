---
name: simplify
description: Clean up recently changed code - reuse existing helpers, remove needless complexity, cut wasted work, fix symptom-level patches at the right layer - then apply the edits without changing behavior. Use for "simplify this", "clean up my changes", "refactor the diff", "make this leaner" after a feature or fix.
---

# Simplify Changed Code

Goal: make the code in the current change smaller, clearer, and cheaper **without changing
what it does**, then apply those edits yourself.

Not this skill's job:
- hunting bugs -> `code-review`
- scope / YAGNI audits of a task -> `strict-review`
- security -> `security-review`

If you notice a real bug while simplifying, do not fix it silently - list it in the
summary and leave behavior alone.

## 1. Collect the change

Default scope is everything on the branch since its base plus uncommitted work. If the
user named a commit, branch, or path, use that instead.

```bash
git status --short
base=$(git merge-base HEAD @{upstream} 2>/dev/null || git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main)
git diff --stat "$base"
git diff "$base"
git ls-files --others --exclude-standard   # new untracked files: read them whole
```

Only code inside this change is fair game. Surrounding code is context, not a target.

## 2. Review through four lenses

Go through the diff once per lens. For each issue note `file:line`, what is wrong, the
concrete cost (duplicated logic, wasted calls, harder to change), and the simpler form.

### Reuse
Does the new code re-implement something the project already has? `grep`/`glob` for
utility modules, shared helpers, base classes, and neighbouring files that solve the same
problem (string/path helpers, retry wrappers, validation, date formatting, API clients,
test fixtures). Also check the standard library and dependencies already installed.
Name the existing function to call instead.

### Simplification
- State that can be derived from other state; flags that duplicate a condition.
- Copy-pasted blocks that differ in one value -> one function or a loop over data.
- Deep nesting -> early returns / guard clauses.
- Abstractions with a single implementation and a single caller; pass-through wrappers;
  config options nobody sets.
- Defensive checks for situations the types or callers already rule out.
- Dead code: unused parameters, imports, variables, branches, commented-out code,
  debug prints left behind.
- Hand-rolled loops where a comprehension/builtin says it directly.

### Efficiency
- The same file, query, or network call done repeatedly where one call would do; N+1 queries.
- Independent async operations awaited one after another instead of concurrently.
- Expensive work moved onto startup, import time, or a hot loop.
- Whole collections copied, sorted, or re-parsed when only part is needed.
- Long-lived callbacks/closures that capture a large surrounding scope and keep it alive;
  prefer an object that holds only the fields it needs.

### Altitude
Is the change fixing the cause or patching the symptom? Special cases bolted onto shared
infrastructure (`if name == "foo": ...` in a generic path), retries masking an ordering
bug, or a try/except that hides a broken invariant suggest the fix belongs one layer
deeper. Name the deeper change. Apply it only when it stays small and inside the diff's
area; otherwise report it as a recommendation.

**Large diffs:** you may run the four lenses in parallel with `delegate_parallel` using
the read-only `analyst` agent - one task per lens, each including the repo path, the
exact diff command, and the lens text above. You still apply the fixes yourself.

## 3. Decide what to apply

Merge findings that point at the same code. Then for each one:

- **Apply** when the result is behavior-preserving, local to the change, and clearly better.
- **Skip** (and say so) when the fix would:
  - change observable behavior, public interfaces, or output formats,
  - require edits well outside the changed area,
  - be a matter of taste rather than a concrete cost,
  - or when you are not sure it is right.

Don't argue with a skipped finding - one line on why is enough.

## 4. Apply carefully

- Use `edit` for each change; keep each edit focused.
- Match the surrounding style (naming, formatting, import order).
- After the edits, run the cheapest checks that cover the touched code: the project's
  formatter/linter if configured, and the focused tests for those modules. If anything
  fails because of your edit, fix it or revert that edit.
- Do not commit.

## 5. Summarize

```
## Simplify: <target>

Applied
- path/file.py:40 - replaced hand-written retry loop with existing `utils.retry()`
- path/api.ts:88 - awaited the two independent fetches concurrently

Skipped
- path/core.py:120 - moving the special case into the dispatcher would touch 6 callers outside this change (recommended follow-up)

Checks: <commands run and their result>
Noticed (not changed): <possible bugs, if any>
```

If the code was already clean, say so in one line and list what you looked at.

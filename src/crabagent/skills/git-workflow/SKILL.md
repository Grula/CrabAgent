---
name: git-workflow
description: Git safety and history rules - never commit, amend, push, tag, rebase, reset --hard, or discard changes unless the user asked for that exact operation; snapshot the workspace before any authorized discard; handle lock files and corrupt state without force. Use before writing Git history, committing, or recovering Git state.
---

# Git Workflow and Safety

The working tree belongs to the user. Make the change, leave it visible, and let them
decide what becomes history.

## Never without an explicit ask

The user must have asked for that exact write in this session, in their own words. A
request to create a new commit counts anywhere in the user's own task text, but it is not
a request to amend, push, or tag. Finishing a task is not authorization: leave your work
uncommitted for review.

- `commit`, `commit --amend`, `push`, `tag`, `rebase`, `cherry-pick`, `revert`, `merge`,
  branch deletion, `reset --hard`, `restore`/`checkout -- <path>` over local edits, and
  `clean -f` all require an explicit ask.
- Never publish (push, open a PR, create a release) merely because it would be useful.
- Never pass `--force` / `--force-with-lease` to `push`, `--no-verify` to skip hooks, or
  change `git config` unless the user asked for exactly that.

## Always fine

Read-only inspection: `status`, `log`, `show`, `diff`, `blame`, `branch -v`, `show-ref`,
`rev-parse`, `for-each-ref`, `merge-base`, `stash list`.

Also fine when you put the original state back afterwards: staging named files to look at
`diff --cached`, and a temporary stash around a tool that requires a clean tree.

## Making an authorized commit

1. `git status --short` and `git diff` - know exactly what is in the tree. If it contains
   changes you did not make and cannot explain, stop and ask before committing.
2. Stage only the files that belong to this commit, by name. Do not use `git add -A` /
   `git add .` in a tree you did not clean. Use `git add -p` when one file mixes units.
3. Match the project's message style: check `git log --oneline -15` (conventional commits,
   ticket prefixes, tense). Subject line under ~72 characters, body explains *why*.
4. If a pre-commit hook fails, fix the cause and create the commit again. Do not bypass it
   and do not `--amend` a commit that did not happen.
5. If a plan or the user divided the work into several commits, keep that split and order.
6. After committing, show `git log --oneline -3` and the final `git status --short`.

Push only when asked, and never force-push a shared branch without explicit instruction.

## Before an authorized discard

Recovery is insurance, not authorization. A snapshot never widens what the user allowed
you to delete or overwrite.

When the user explicitly authorized an operation that may discard or broadly overwrite
workspace changes (`reset --hard`, `checkout`/`restore` over edits, `clean`, a rebase
with a dirty tree), and the workspace is an ordinary Git checkout:

1. Save a snapshot with the script in this skill's directory (the path is printed after
   the skill content as "Skill directory"):

   ```bash
   bash <skill-dir>/scripts/workspace-recovery.sh save before-discard
   ```

2. Keep the printed `refs/crabagent/recovery/...` ref in your final message, then perform
   only the exact authorized operation.
3. If the save fails or the workspace is unsupported, stop before anything destructive
   and ask the user.

To bring that saved tree back later:

```bash
bash <skill-dir>/scripts/workspace-recovery.sh restore <recovery-ref>
```

Restore first snapshots the current workspace, keeps HEAD, the branch, and the real index
as they are, and writes the saved files into the working tree. The snapshot also keeps
staged content that differed from the working copy: read it with
`git show '<recovery-ref>^2:<path>'` (the second parent is the saved index). Ignored files
are not saved; restore refuses to overwrite an ignored path rather than lose data it could
not save. Pass the exact printed ref, not a revision expression.

## Locks and corrupt state

An `index.lock` or other lock file usually means another process is writing.

- Never kill the holding process on your own initiative.
- Wait and retry, inspect the holder read-only (`ps`, `lsof`), use that tool's normal stop
  mechanism, or report the block to the user.
- Never delete a lock file without proving no live process owns it.
- Never respond to index or object corruption by deleting state and committing anyway.

## Other rules

- Don't run `git init` inside vendored, build, data, or already version-controlled trees.
- Don't hand-edit generated files; change their source of truth.
- Scratch repositories under a temporary directory are yours to experiment in.

## If you already made an unwanted write

Say so plainly and stop. Do not attempt more history surgery without direction; offer the
recovery options (reflog entry, recovery ref) and let the user choose.

<!-- Adapted from Meta muse-code skills/git (Apache-2.0). Modified for CrabAgent. -->

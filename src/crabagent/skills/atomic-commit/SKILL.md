---
name: atomic-commit
description: Create one clean Git commit containing exactly the files of a single finished, verified task, with a message in the repository's style. Use when the user asks to commit, or when a task workflow says the verified task should now be committed.
allowed-tools: bash read
---

# Atomic Commit

One task, one commit, nothing else in it. Committing is an action the user must have asked for, either directly or through a workflow they set up. If nobody asked, stop and ask instead.

## Before you start
- The task is done and verified. If tests or checks haven't run since the last edit, run them now (see the `verify` skill). Never commit work that fails its checks.
- You know which files belong to the task. If you're unsure, list the candidates and ask.

## 1. Inspect the working tree
Run these together:

```bash
git status --short
git diff --stat
git diff --cached --stat
git log -n 5 --format='%s'
```

- Anything already staged that doesn't belong to this task: unstage it with `git restore --staged <path>`. That keeps the changes in the working tree.
- Unrelated modified or untracked files stay out of the commit. Leave them exactly as they are. Never revert, stash or delete someone else's changes to make the tree look clean.

## 2. Stage exactly the task's files
- Stage by explicit path: `git add path/one path/two`. Never use `git add -A`, `git add .` or `git commit -a`, which sweep in unrelated work.
- If one file mixes this task's changes with unrelated ones, tell the user and ask how to proceed. Don't silently commit the unrelated hunks.
- Review what will be committed with `git diff --cached`. Look for:
  - debug output and commented-out code
  - stray files
  - secrets: API keys, tokens, `.env` contents, private keys. If you find any, stop and tell the user.

## 3. Write the message
- If the task has an ID (from a plan, ticket or task list), use `task(<TASK_ID>): <summary>`. Example: `task(P3-6): enforce one-directional layering in API`.
- Otherwise follow the convention visible in `git log`, for example `feat: …` or `fix: …`.
- Summary line:
  - imperative mood, about 72 characters at most
  - says what changed for the reader, not how you worked
- Add a body (after a blank line) only when the diff doesn't make the reason obvious: why the change was needed, and any trade-off.
- Pass multi-line messages through a heredoc so quoting can't break them:

```bash
git commit -F - <<'EOF'
task(P3-6): enforce one-directional layering in API

Route handlers imported repositories directly, bypassing the service layer.
EOF
```

## 4. Commit and confirm
- Run the commit. If a pre-commit hook fails:
  1. Read its output and fix the cause.
  2. Re-stage the fixed files and commit again.
  3. Never bypass hooks with `--no-verify`.
  4. Don't `--amend` a commit you didn't just create unless the user asks.
- Confirm with `git log -1 --stat` that the commit contains exactly the intended files.
- Confirm with `git status --short` that only the unrelated changes you deliberately left out remain.

## Rules
- One task per commit. If the staged changes cover two tasks, split them into two commits.
- Don't push, open a pull request or touch other branches unless the user asks.
- Report the commit hash, its summary line and the files it contains.

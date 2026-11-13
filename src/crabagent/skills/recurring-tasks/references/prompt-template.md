# Scheduled prompt template

Fill this in and pass it as `question` to `scheduled_task_create`. The run sees
nothing else, so write it for someone with no context at all.

```
SCHEDULED TASK: <name>. Runs <plain-language schedule>.
Purpose: <why the user wants this, in one sentence>.

Inputs:
- <exact URL / absolute path / repo / query / mailbox folder>
- State file: <absolute path>/.crabagent/state/<slug>.json
  (JSON: {"seen": [<dedupe keys>], "last_run": "<ISO time>"})

Steps:
1. Read the state file. If it does not exist, this is a baseline run: collect the
   current items, save them as seen, reply "Baseline recorded: <n> items." and stop.
2. <Fetch / check step, naming the tool, e.g. web_scrape the URL, email_check, bash
   `git -C /abs/repo log --since=...`>
3. Find the items whose key (<dedupe key>) is not in "seen".
4. <Filter: what counts as noteworthy>
5. Write the report (format below), then update the state file with the new keys
   and last_run.

Rules:
- Read-only except for the state file. Do not delete, send, purchase or push
  anything. <Or: the one action the user explicitly approved.>
- If a source fails, say which one and why in the report, and keep the state
  unchanged for that source.
- Be brief. No preamble.

Report format (your final message):
- Nothing new → exactly: "No changes since <last_run>."
- Otherwise:
  **<Name> — <date>**
  - <item>: <one-line why it matters> (<link>)
  ...
  Next check: <next scheduled time>.
```

## Short form, for stateless tasks
Use this for jobs that need no memory between runs, such as a daily briefing:

```
SCHEDULED TASK: <name>. <Goal in one sentence>.
Do: <steps with tools and exact sources>.
Constraints: read-only; finish within a few minutes; cite links.
Final message format: <format>. If nothing relevant: "<quiet line>".
```

## Self-deleting reminder

```
SCHEDULED TASK: reminder. Reply with this reminder for the user:
"<reminder text>"
Then call scheduled_task_delete with task_id <N>.
```
(Create the task first, then use `scheduled_task_update` to insert the real id.)

## Prompt checklist
- [ ] Every source is a full URL or an absolute path.
- [ ] "New" has an exact definition and a dedupe key.
- [ ] The quiet output for "nothing changed" is written out.
- [ ] No destructive actions unless the user explicitly requested them.
- [ ] A typical run finishes well inside 10 minutes.

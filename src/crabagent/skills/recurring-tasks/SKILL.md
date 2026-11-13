---
name: recurring-tasks
description: Turn requests like "every morning…", "check X every hour", "remind me on Fridays", "send me a weekly digest" into scheduled tasks with self-contained prompts, correct cron, idempotent state and clear reports. Also for listing, changing, pausing or deleting schedules.
---

# Recurring Tasks

CrabAgent runs scheduled work with the `scheduled_task_*` tools. Each schedule pairs
a **prompt** with a **5-field cron expression**. When the cron fires, a fresh agent
session runs the prompt.

## How a scheduled run behaves (design for this)

- **The run starts with nothing but the prompt.** It is a brand-new conversation with
  no chat history and no knowledge of why the task was created. Anything it needs
  must be written into the prompt.
- **Tools run without confirmation.** No one is there to approve them. Never schedule
  deletes, payments, sends to third parties or force-pushes unless the user asked
  for exactly that action. Where it applies, the prompt should say "read-only".
- **10-minute limit per run.** Keep each run small. Split heavy jobs into separate
  schedules.
- **Timezone.** Cron is evaluated in the **server's local timezone**. If the user
  states a time in a different zone, convert it and say so.
- **Working directory** is the server's working directory. Use absolute paths for
  any files the task reads or writes.
- **Output.** The user gets a notification ("Scheduled task completed") that links
  to the run's conversation. The run's **final message is the report**. If email
  tools are configured and the user wants delivery, the prompt can say to send the
  result with `email_send`.
- **Memory tools may not work** in scheduled runs, because there is no user identity
  attached. Keep state between runs in a file (see Idempotency).
- **No one-shot runs.** Every schedule repeats. For a single reminder, use the
  self-deleting pattern below.

## Workflow

### 1. Pin down the request
Pull these out of the user's message. Ask only about details that change the outcome
(with `ask_question` when you have it, otherwise in your reply):
- **What**: the job, its sources, and what counts as noteworthy.
- **When**: the frequency and time. Read "morning" as 08:00 and "weekdays" as
  Mon–Fri, and say which defaults you assumed.
- **Output**: what the report contains, and whether it should stay quiet when
  nothing changed.
- **Stop**: when the task should end, if ever ("until the PR merges", "for two weeks").

### 2. Check for duplicates
Call `scheduled_task_list`. If a similar task exists, offer to update it with
`scheduled_task_update` instead of creating a second one.

### 3. Write the prompt
Use [references/prompt-template.md](references/prompt-template.md). A good scheduled
prompt:
- states the goal and the exact sources: URLs, absolute paths, repos, mailbox, query
  strings;
- spells out the steps and which tools to use (`web_search`, `web_scrape`,
  `email_check`, `bash`, `read`…);
- defines "new" or "changed" against a state file, so the run doesn't repeat itself;
- fixes the report format, including the line to output when nothing changed;
- sets safety limits: read-only, no destructive actions, and what to do if a source
  fails;
- ends by telling the run to write the final message as the report.

### 4. Build the cron expression
Format: `minute hour day month weekday`, exactly 5 space-separated fields. The rules
are in [references/cron-cheatsheet.md](references/cron-cheatsheet.md). The traps that
matter most:
- **Weekdays: use names** (`mon-fri`, `sat,sun`). In this scheduler numeric weekdays
  start at **0 = Monday**, so `1-5` means Tue–Sat.
- Setting both `day` and `weekday` means the run fires only when **both** match.
  This differs from classic cron, where either match fires the run.
- No spaces inside a field.
- Every-few-minutes polling (`*/5`) costs a full agent run each time. Suggest the
  slowest interval that still meets the need.

### 5. Create and confirm
1. Call `scheduled_task_create(name, question, cron_expression)`. `model` is optional
   and defaults to the current model. Pass it only if the user wants a specific or
   cheaper model.
2. Call `scheduled_task_get(task_id)` and report the cron in plain words and the
   **next run time** shown there.
3. Offer a dry run. Run the prompt's steps once now, in this conversation, so the
   user sees a sample report. This also creates the initial state file, which avoids
   a flood of "new" items on the first scheduled run.

## Idempotency and state
For any "tell me what's new" task:
- Keep a state file at an absolute path, for example
  `<workspace>/.crabagent/state/<task-slug>.json`. Store the last-seen IDs or
  timestamps and the last run time.
- Each run reads the state, finds the items not seen before, reports them, and then
  writes the updated state. If the run failed before reporting, it must not write
  state.
- If the state file is missing, treat the run as a baseline: record the current state
  and report "baseline recorded" instead of dumping everything.
- Fix the dedupe key, such as a URL, an ID, or a hash of title plus date. Don't
  compare by fuzzy wording.

## One-time reminders and self-ending tasks
1. Create the task with the cron set to the target minute, hour, day and month, for
   example `0 15 14 11 *`.
2. Read the returned task id (`#N`). Then call `scheduled_task_update` to append this
   to the prompt: "After completing the above, call `scheduled_task_delete` with
   task_id N."
3. Use the same pattern for tasks that should stop when a condition is met, such as
   "if the PR is merged, report it and delete task N".

## Managing schedules
- **See them**: `scheduled_task_list` truncates prompts. `scheduled_task_get` shows
  the full prompt, the next and last run, and the last error.
- **Change them**: `scheduled_task_update` accepts only `name`, `question` and
  `cron_expression`. `question` **replaces** the whole prompt, so read the full
  prompt with `scheduled_task_get` first and edit that text.
- **Pause, resume or delete**: `scheduled_task_pause`, `scheduled_task_resume` and
  `scheduled_task_delete`. Confirm with the user before deleting.
- **Debug a failing task**: look at the `last_error` from `scheduled_task_get`, fix
  the prompt or the path, then dry-run the prompt here.

## Reply after setup
Keep it short. Include the name and id, the schedule in plain words with the
timezone, the next run time, what each run reports and where to find it, any
assumptions you made, and how to pause the task or change the time.

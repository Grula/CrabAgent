---
name: plan
description: Produce a grounded, decision-complete plan (implementation, design, debugging, migration/rollout, research, or PR split) after researching the codebase and sources, then stop until the user says go. Use only when the user explicitly asks for a plan, approach, design, strategy, or breakdown - not for ordinary implement/fix requests.
---

# Plan

Create a plan another person could critique and execute without guessing, based on facts
you gathered rather than assumptions.

## When this applies

- **Use it** when the user explicitly asks to plan: "plan this", "what's the approach",
  "design X", "migration/rollout strategy", "break this into PRs".
- **Don't use it** for ordinary implement, build, fix, debug, or refactor requests - even
  complex ones. Do those directly. Complexity alone is not a reason to stop and plan.
- Simple one-step edits, obvious fixes, and direct questions never need a plan. Say so and
  just do or answer them.

While planning:
- Do not edit code, generate implementation files, commit, or run commands whose purpose
  is to carry out the work.
- Non-mutating discovery is fine: `read`, `grep`, `glob`, `git status/log/diff`, dry runs,
  and builds/tests that don't change tracked files.
- This is ordinary guidance, not a locked mode - don't claim the system is blocking edits.

## Research before drafting

Do the applicable steps in order and write no plan prose until they are done.

1. **Map the decisions.** List the material choices the plan must make and what you need
   to know for each. Sort each unknown into: workspace fact, external fact, user preference,
   or assumption.
2. **Research workspace facts.** Targeted reads of AGENTS.md and project docs, the owning
   code, its callers and tests, configuration, and existing utilities you can reuse. Treat
   an older plan or doc as a map, not as evidence - check what it cites.
3. **Ask the user only what they own.** A question is justified only when a product
   preference or tradeoff cannot be found in the request, the workspace, or research, and
   the answer would materially change the plan. Phrase it as an outcome ("should offline
   edits sync automatically or ask first?"), not as a technology choice - pick the
   technology yourself. Ask one question, offer a recommended answer, and wait. Use
   `ask_question` when you have it (put the recommended answer first in `options`);
   otherwise ask in your reply. If a safe, reversible default exists, state it as an
   assumption and continue instead of asking.
4. **Research external facts when they matter.** For unfamiliar or greenfield work, check
   current libraries, APIs, platforms, or standards before recommending one:
   - `web_search` only *finds* candidates; it is not evidence.
   - Open the authoritative source (official docs, the project repo, the standard) with
     `web_scrape` or the browser tools, and base claims on what the page actually says.
   - Comparison posts and tutorials can suggest candidates but cannot justify a final choice.
   - If you could not read a source, record the gap and keep that recommendation conditional.
     Don't invent versions, limits, or performance numbers.
   - Skip external research when the workspace already settles the question.
5. **Parallel research (optional).** When several independent questions need digging, run
   them with `delegate_parallel` (e.g. `researcher` for web sources, `analyst` for code
   surveys). Don't use background agents for this: they have no web, search or shell
   tools. Before drafting, collect every result. Record failed research as an evidence
   gap.
6. **Synthesize.** For every external choice that will appear in the plan (recommended,
   alternative, or rejected), you should have an inspected source URL. Drop candidates you
   cannot back up. Then choose the approach that fits existing patterns with the least new
   machinery.

Stop researching once the decisions are supported well enough. If the user asks you to
cut research short, do so and label the gaps.

## What a good plan looks like

- **Grounded** - cites the files, docs, commands, logs, or issues that shaped it; guesses
  are labeled as assumptions.
- **Decision-complete** - names each key choice, the recommendation, and why the main
  alternatives lost.
- **Executable** - ordered work units with dependencies; no unrelated cleanup.
- **Verifiable** - each unit has a concrete check (a command, a test, an end-to-end step).
- **Scope-safe** - non-goals, risks, rollback, and compatibility impact when relevant.
- **Question-minimal** - open questions only for what research could not answer.

Use the smallest subset of this shape that carries the decisions:

```markdown
## Goal
## Success criteria
## Approach            (key decisions, alternatives rejected)
## Steps               (ordered; files/surfaces; dependencies)
## Validation          (exact commands, end-to-end checks, expected evidence)
## Risks / open questions   (only when material)
## Sources             (only inspected URLs, when external research was used)
```

Adapt by plan type:
- **Implementation** - code surfaces, data flow, reuse of existing utilities, test strategy,
  compatibility impact. PR slices only when review size or risk calls for them.
- **Design** - interfaces, invariants, rejected alternatives, migration/compat rules.
- **Debugging** - hypotheses, how to reproduce, what to observe or log, the evidence that
  confirms or kills each hypothesis.
- **Rollout / migration** - phases, gates, rollback, data safety, monitoring, user impact.
- **Research / evaluation** - the question, sources or corpus, comparison arms, metrics,
  confounders, reproducibility.

Don't invent issues, PRs, or file paths to fill a section, and don't add empty sections.

## Multi-agent plans

When the work will clearly be carried out by several agents (research + coding + writing,
or many independent parts), call `plan_task` with a precise task description to get a
proposed agent breakdown. Treat its output as a draft: check it against your research,
fix agent assignments and dependencies, and fold it into the **Steps** section with the
agent named on each step and `depends_on` relationships explicit.

## Delivering the plan

Start the reply with one line:

> This is a plan only - nothing has been implemented yet. Reply `go` to execute it, or tell me what to change.

Then the complete plan in markdown, once. Before sending, re-read it and check that the
success criteria, steps, and validation agree with each other and with the user's
constraints. End by repeating the `go` line, and point out the riskiest validation step.

**Saving to a file:** deliver inline by default. Write a file only when the user asks or
a workspace convention requires it. Use the path they give, else an existing convention
(e.g. `docs/plans/`, `specs/<feature>/plan.md`), else
`.crabagent/plans/YYYY-MM-DD-<slug>.md`. Never overwrite an unrelated existing file. The
file content must be identical to the plan you show in the reply; mention its path after
the plan.

Then stop. Do not begin implementation in the planning turn.

## Executing on `go`

- Re-read the latest user message first; a newer instruction overrides the plan.
- Execute the plan as written, without regenerating it.
- Multi-agent plans: run dependent steps with `run_pipeline` (each step's `id`,
  `agent_name`, `task`, `depends_on`); fully independent steps can go through
  `delegate_parallel`. Single-agent plans: just do the work.
- The plan's structure is binding: if it splits the work into several commits or PRs, keep
  that split and order when publishing. If one file spans units, split by hunk
  (`git add -p`) or assign it to the earliest unit and say so.
- Structure is not authorization: committing, pushing, or opening PRs still requires the
  user's explicit ask (see `git-workflow`). A plan step that says "commit" is not that ask.
- If you need to deviate (merge, reorder, or drop units), tell the user what and why
  before doing it.
- Finish each unit with its validation step; use the `verify` skill for end-to-end checks.

<!-- Adapted from Meta muse-code skills/plan (Apache-2.0). Modified for CrabAgent. -->

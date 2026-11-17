---
name: grill
description: Clarify requirements before building - ask the one blocking product question when a request leaves a material choice open, or run a full decision interview ("grill me", "interrogate this spec") one question at a time with a recommended answer, optionally recording settled decisions in a project doc. Use when a spec is unclear or the user asks to be grilled.
---

# Grill: clarify requirements before building

Settle the decisions only the user can make, research everything else yourself, and
leave a written record of what was agreed.

This skill has two modes. Pick one from the request.

| Mode | When | Output |
|---|---|---|
| **Quick clarification** | You are about to build something and the request still leaves one material, user-owned choice open | One question (or up to three tightly coupled ones), then you proceed |
| **Full interview** | The user explicitly asks to be grilled, to stress-test a spec, or to work through decisions before a plan | A sequence of single questions, a settled contract, optionally a decision document |

Complexity, ambiguity, or a greenfield project on their own do **not** trigger either mode.

## Ground rules (both modes)

1. **Research before asking.** Read the request, named documents, AGENTS.md, issues, specs,
   and relevant code. Never ask for something you can find out yourself.
2. **Ask only about what the user owns:** scope, target users, user-facing behavior,
   platform support, data/API contracts, domain values, budgets, where the project lives,
   cross-system architecture, and design choices that set the direction and are costly
   to undo later (architecture, data model, public API, a framework or major
   dependency). Details that are cheap to change later (algorithm, file layout,
   internal helpers, naming) are yours - decide them and say what you chose.
3. **Prefer a stated default.** If a narrow, reversible default safely satisfies the
   request, state it as an assumption and keep going instead of asking.
4. **One decision per question,** phrased as an outcome, with your recommended answer
   first and a one-line reason. Ask with `ask_question` when you have it: pass 2-4
   short, mutually exclusive `options` with the recommended one first, and set
   `multiple` only when several can apply. Without that tool, list the options in your
   reply and invite the user to pick, modify, or discuss them.
5. **Never re-ask** something already answered in the request or earlier in the conversation.
6. **Never ask for permission to start,** and never turn a clarification into a refusal.

## Mode 1 - Quick clarification

1. Check only the request and the requirements it names or that govern it. Don't scan the
   whole repository hunting for hypothetical ambiguity. If that evidence settles the
   outcome, proceed without asking.
2. If one material choice remains, ask before any substantive commitment (a scaffold, a
   public API, a schema, domain values baked into code). Reading files and taking notes
   before the answer is fine.
3. Ask one blocking question, or at most three tightly coupled ones in a single message,
   each with a recommendation. Then wait.
4. After the answer, apply it and continue. Don't ask again unless new evidence reveals a
   genuinely different fork.

Standalone plan or design deliverables belong to the `plan` skill; this mode does not add
an approval gate to work whose expected behavior is already clear.

## Mode 2 - Full interview

### Before the first question
- Research the discoverable facts (step 1 of the ground rules).
- Decide whether the user wants a durable record. If they asked for docs - or the project
  keeps decision records (ADRs, `docs/decisions/`, a spec folder) and they want one - resolve
  the target file from their wording and the repo's existing conventions. If it can't be
  determined, make that your first question. Don't invent a generic location.
- Sketch the decision tree privately: which decisions depend on which.

### During the interview
- Ask one decision-forcing question per message, recommended answer first. Wait for the reply.
- Follow dependent decisions down the tree until it is exhausted; you decide when that is.
  Don't end with "anything else?" - finish with the summary instead.
- Use a comparison table only when the user asks for one.
- When recording: write each decision into the document as soon as it is settled, marked
  **Draft**. Keep unresolved questions visibly marked as open.
- If the interview is interrupted, keep the Draft and all edits as they are; never revert them.

### Scope contract
The interview is not finished until the scope is fixed in writing and the user accepts
that text explicitly:

1. **Boundary** - what the deliverable is (documents, directories, features, PRs) and which
   kinds of work are out of scope (e.g. follow-up specs, migrations, runtime code).
2. **Done means** - a short checklist of observable conditions that finish the work.
   Nothing outside the checklist is a dependency for completion.
3. **Staged work is approved one stage at a time.** Later stages described in the record
   are deferred proposals; accepting the record doesn't approve them.
4. **"Go" never widens scope.** If execution later needs work outside the boundary, stop
   before doing it and either get the user's explicit approval for the wider boundary or
   propose it as a separate follow-up.

If the work is tracked in an issue or ticket, offer to post the accepted scope there,
quoting the user's acceptance.

### Wrap-up
Summarize the settled contract: goals, non-goals, decisions (with the reasoning),
constraints, risks, how it will be validated, and anything still open.

- A document stays **Draft** until the user explicitly accepts it; only then mark it
  **Final**. Running out of questions is not acceptance.
- If the user asked for documentation, the session isn't done until a useful document was
  created or updated - "no docs needed" with no file change does not satisfy that request.
- List every documentation path you changed and whether it is Draft or Final.
- If there is no document but the decisions should survive the session, offer to store a
  short summary with `memory_save`.

Ending the interview never authorizes implementation. Build only after a separate,
explicit request - then the `plan` skill or direct implementation takes over.

<!-- Adapted from Meta muse-code skills/grill, grill-and-record and requirements-clarification (Apache-2.0); interview approach credited there to Matt Pocock's "grilling" skill (MIT). Modified for CrabAgent. -->

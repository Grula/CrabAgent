---
name: write-spec
description: Turn a request into SPEC.md, the contract people build and test against (rules, interfaces, errors, worked examples), after researching and asking the user only what they own. Use when asked to write or update a spec, or before building a feature whose behaviour isn't written down.
---

# Write a spec

A spec says **what** to build and how to tell that it is right. A plan (the `plan`
skill) says how to build it. The spec is what the tester turns into tests, the coder
builds against, and the reviewer checks against, so every rule in it must be testable.

## 1. Start from what exists
- Look for an existing spec: `SPEC.md`, `SPECS.md`, `docs/spec*.md`. Use its file and
  section style. Also read `AGENTS.md` and the README.
- **A spec exists:** you are adding a section for the new work, or changing a rule. A
  rule that is already settled is the user's: change it only after they agree, and say
  which rule changed.
- **Code exists:** find out what it does today where the new work touches it (data
  model, public API, commands, file formats). Read it yourself if it's one known file;
  otherwise send a `scout`, one per area. The spec must fit what exists, or say exactly
  what changes.

## 2. Research before asking
- Facts from outside the repo (a standard, a data source and its format, a library's
  limits, a domain rule such as how money is rounded) go to the `researcher` first, with
  sources.
- Note which questions the research settled and which are still a choice.

## 3. Ask the user what only they can decide
Ask, one decision per question, with 2-4 options, your recommendation first and a one-line
trade-off each (use `ask_question`; see the `grill` skill, quick mode). Put the research
facts and their sources in the options. These belong to the user:
- scope and what is explicitly out of scope
- user-facing behaviour, and what happens in the awkward cases
- data and file formats, public API and command shape (costly to change later)
- domain rules: money, rounding, time zones, permissions, limits

Decide everything else yourself (names, internal structure, small reversible details) and
list it under Assumptions so the user can overrule it. Never ask what the code or the
research already answers.

## 4. Write it
Use these sections, dropping any that don't apply:

1. **Purpose:** one short paragraph. Who it is for and what problem it solves.
2. **Scope:** what is included, and a list of what is not.
3. **Concepts:** each term with a one-line definition. Code and tests use these words.
4. **Rules:** numbered, testable statements ("When X, Y happens"), including edge cases.
   Exact values, never "should usually".
5. **Interfaces:** function signatures, commands and options, file or wire formats, with
   one small example each. Exact names.
6. **Errors:** each error and when it happens.
7. **Worked examples:** a table of concrete inputs and the exact expected results. These
   become the first tests, so check every number yourself.
8. **Decisions and assumptions:** what was decided, by whom (the user or you), and why.
   Research facts go here with their source URLs.
9. **Later:** related work deliberately left for another time.

Describe behaviour, not implementation. Leave out file layout, class design and
algorithms unless they are part of the contract (a rounding rule, a file format).

## 5. Check before writing
- Can a test check every rule?
- Do the worked examples follow the rules? Recompute them.
- Is every open question either answered by the user or listed as an assumption?
- Are terms and names used the same way in every section?

## 6. Write the file
- With `write`/`edit`: write it, then read it back.
- **Without file tools (an architect):** give one `coder` task containing the full text:
  "Write exactly this content to SPEC.md" (or "add this section after section X; change
  nothing else"), and say it must not reword anything. Then `read` the file back to
  confirm it landed as written.

## 7. Report
List the sections written, the decisions the user made, and the assumptions you made, so
the user can overrule them. If the user asked only for a spec, stop there and say what
building it would take. If the spec is the first step of a build, go on: the tester
writes the first tests from the worked examples.

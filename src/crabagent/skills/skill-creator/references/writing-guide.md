# Skill Writing Guide

Read this when drafting a non-trivial skill or when a skill is not performing well.

## Starter template

```markdown
---
name: <kebab-case-name>
description: <What it does>. Use when <trigger phrases / situations / file types>.
---

# <Readable Name>

<One or two sentences: what this skill is for and the outcome it should produce.>

## Workflow

1. <Step — which tool, and why this step matters>
2. <Step>
3. <Verification step: how the agent proves the result is right>

## Rules

- <Constraint and the reason for it>

## Output

<Template or example of the expected result.>

## References

- For <situation>, read `references/<file>.md`.
```

## Patterns that work

**Output template** — when the format matters, show it literally:

```markdown
## Report structure
Use this structure:
# <Title>
## Summary
## Findings
## Next steps
```

**Examples** — one input→output pair often beats a paragraph of rules:

```markdown
**Example**
Input: Added JWT login
Output: feat(auth): add JWT-based login
```

**Variant routing** — when a skill covers several frameworks or providers, keep the selection logic
in SKILL.md and put each variant in its own reference file. The agent then reads only the one it
needs, which saves context:

```
cloud-deploy/
├── SKILL.md          (how to choose + shared steps)
└── references/
    ├── aws.md
    ├── gcp.md
    └── azure.md
```

For reference files longer than ~300 lines, add a short table of contents at the top.

## Progressive disclosure, in CrabAgent terms

| Level | When it is in context | Budget |
|---|---|---|
| `name` + `description` | Always (inside the `skill` tool's catalog) | ~1–3 sentences |
| SKILL.md body | After the agent calls `skill` | roughly 60–200 lines |
| Auxiliary files | Only when the agent `read`s or runs them | as needed, ≤10 files |

Every skill's description is paid for on every request, so keep it tight and specific.

## Description tuning

Draft ~10 test prompts: about half should trigger the skill, half should not.

- Make them realistic: file paths, personal context, casual phrasing, typos. "Format this data" is
  a useless test; "my boss sent Q4-sales-final-v2.xlsx and wants a margin % column, revenue is in C"
  is a good one.
- The best negatives are near-misses that share keywords but need something else (for a PDF skill:
  "convert this PDF chart into an Excel table" might belong to a spreadsheet skill).
- Run each prompt in a fresh session and record whether the skill was loaded. Adjust wording
  (add missing trigger phrases, add an exclusion for a recurring false positive) and re-run.
  Avoid overfitting: a change should make sense for prompts you did not test.

## Improving a skill after test runs

1. **Generalize.** The skill will be used on many prompts you never saw. Prefer a different framing
   or a clearer principle over a narrow patch for one example.
2. **Stay lean.** Delete instructions that are not pulling their weight. If transcripts show the
   agent doing busywork, find the sentence that caused it.
3. **Explain the why.** If you are reaching for ALWAYS/NEVER in capitals, try explaining the reason
   instead; reserve hard rules for safety and genuinely fragile steps.
4. **Bundle repeated work.** If every run wrote the same helper script, put it in `scripts/` and
   tell the skill to run it — or turn it into a custom tool with `create_tool`.
5. **Compare.** When unsure whether a revision helped, run the same prompts with the old and new
   versions and compare outputs side by side.

Stop iterating when the user is satisfied, feedback is empty, or changes stop making a difference.

## Custom tool vs. bundled script

| Need | Use |
|---|---|
| Agent should call it like any other tool, with typed parameters | `create_tool` (saved in `.crabagent/tools/<name>.py`, must define `run(...) -> str`) |
| Helper only meaningful inside this skill's workflow | `scripts/` in the skill folder, run with `bash` |
| Text the agent needs to read and follow | `references/` |
| Files copied into the output (templates, logos) | `assets/` |

Custom tools require user permission to run by default; keep them small, standard-library-only
where possible, and return a string.

<!-- Adapted from Anthropic claude-code/skills/skill-creator (Apache-2.0). Modified for CrabAgent. -->

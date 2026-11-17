---
name: project-init
description: Analyze the current codebase and write (or refresh) the workspace AGENTS.md with update_agents_md - real build/test/lint commands, big-picture architecture, non-obvious conventions and gotchas, kept short. Use for "init this project", "create/update AGENTS.md", "document this repo for the agent", or when starting work in an unfamiliar repo.
---

# Project Init: write AGENTS.md

AGENTS.md at the workspace root is injected into the system prompt of **every** session
in this workspace. Whatever you write costs context forever, so it must contain only what
an agent would otherwise get wrong or waste time rediscovering.

Rules of the file:
- Written only through `update_agents_md(content)`, which **replaces the whole file**.
- Only the workspace-root AGENTS.md is loaded; content beyond ~16,000 characters is
  truncated. Aim for well under that - typically 40-150 lines.
- The current conversation keeps the system prompt it started with, so a new AGENTS.md
  takes effect in new conversations. Tell the user this when you finish.

## 1. Check what already exists

`read` `AGENTS.md` at the workspace root.

- **Exists:** you are refreshing, not rewriting. Keep every still-correct line the user
  wrote. Plan targeted additions, corrections, and removals (section 5).
- **Missing:** you are creating it.

## 2. Survey the codebase

Read, in roughly this order, whichever exist:

1. Manifests and tooling: `pyproject.toml`, `setup.cfg`, `package.json` (+ workspaces),
   `Cargo.toml`, `go.mod`, `pom.xml`/`build.gradle`, `*.csproj`, lockfiles (which package
   manager), `Makefile`, `justfile`, `Taskfile.yml`, `noxfile.py`, `tox.ini`.
2. CI configuration (`.github/workflows/`, `.gitlab-ci.yml`, etc.) - the most trustworthy
   source of the commands that really build, lint, and test the project.
3. README, CONTRIBUTING, docs index, architecture or ADR docs.
4. Lint/format/type-check configs (ruff, black, eslint, prettier, biome, mypy, tsconfig,
   rustfmt, golangci) - note only settings that differ from the tool's defaults.
5. Test layout and config: where tests live, fixtures, markers, how to run one test.
6. Environment: `.env.example`, docker-compose, required services, env vars, ports.
7. Rule files left for other coding assistants, e.g. `CLAUDE.md`, `GEMINI.md`,
   `.cursorrules`, `.cursor/rules/`, `.github/copilot-instructions.md`, `.windsurfrules`,
   `.clinerules`. Carry over the parts that are still accurate.
8. The top-level source layout and the entry points (`main`, CLI, app factory, router).
   Read enough to explain how a request or command flows through the main layers.
9. Existing workspace skills in `.crabagent/skills/` - don't duplicate their content.

For a large monorepo you may survey packages in parallel with `delegate_parallel` using
the read-only `the_reviewer` agent (or another read-only agent from your team list) - one
task per package, each asking for: commands, entry
points, internal dependencies, unusual conventions, and gotchas, with file references.

## 3. Confirm the commands

A wrong command in AGENTS.md misleads every future session. For each build/lint/test
command you plan to list, run it when it is cheap and harmless (e.g. `--help`, a lint
run, a single fast test). Do not run deploys, migrations against real data, or anything
that publishes. Mark any command you could not confirm, or leave it out.

## 4. Fill gaps by asking - sparingly

If something important cannot be learned from the code (branch/PR conventions, which of
two test setups is current, required credentials), ask the user - at most three short
questions. Use `ask_question` when you have it (one call per question, with `options`
when the answer is a choice); otherwise ask them all at once in your reply. If nothing is blocking, skip this and proceed.

## 5. Draft the content

Test every line: *would an agent make a mistake or waste time without this?* If not, cut it.

Include:
- Exact commands to install, build, run, lint/format, type-check, test everything, and
  run a **single** test - especially anything non-standard.
- Architecture in a few sentences: main components, how they connect, where the entry
  points are, and anything that requires reading several files to understand.
- Conventions that differ from language defaults (naming, import style, error handling,
  where new modules/tests go, generated files that must not be hand-edited).
- Gotchas: required env vars and services, ordering constraints, slow or flaky tests,
  platform quirks, directories to ignore.
- Repo etiquette if the project has it: branch naming, commit message format, PR rules.

Exclude:
- File-by-file listings or component inventories an agent can find with `glob`.
- Generic advice ("write clean code", "add tests", "handle errors", "don't commit secrets").
- Standard commands obvious from the manifest when nothing about them is special.
- Long tutorials or API references - point to the doc file instead ("see docs/api.md").
- Sections invented without evidence ("Tips", "Common tasks") - only state what you found.

Suggested skeleton (drop empty sections):

```markdown
# AGENTS.md

## Commands
- Install: `...`
- Test all: `...` | single test: `...`
- Lint/format: `...`
- Run locally: `...`

## Architecture
<3-8 sentences or a short bullet list>

## Conventions
- ...

## Gotchas
- ...
```

**Refreshing an existing file:** show the user the proposed changes as a short list of
additions/removals with one reason each before writing. Because `update_agents_md`
overwrites the file, your `content` must include every line you are keeping.

## 6. Write and confirm

1. Call `update_agents_md` with the full content.
2. `read` `AGENTS.md` back to confirm it landed intact.

## 7. Wrap up

Tell the user what you wrote (section by section, one line each) and which commands you
confirmed by running. Then list only the follow-ups that apply to this repo, for example:
- no test setup found -> suggest adding one so changes can be verified;
- a repeated workflow (release, deploy, data refresh) -> suggest a workspace skill in
  `.crabagent/skills/<name>/SKILL.md`;
- conflicting or stale docs discovered during the survey.

Remind them AGENTS.md is a starting point they can edit, and that this skill can be run
again to refresh it after the project changes.

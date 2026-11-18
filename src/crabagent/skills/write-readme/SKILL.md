---
name: write-readme
description: Write or refresh a project's README.md for people - what it is, how to install and run it, and usage examples that were actually run - without inventing features or duplicating the spec. Use when asked to write or update the README, or after a change to how people install or use the project.
---

# Write a README

The README is for a **person** who has just found the project: someone who wants to use
it or start working on it. Agents get their instructions from `AGENTS.md` (see the
`project-init` skill), and the full behaviour lives in the spec, so the README stays short
and points to both.

## 1. Gather the facts
Don't invent anything. Collect:
- the existing README (refreshing it: keep every still-correct line the user wrote)
- the manifest (`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`...), for name,
  version, language version and dependencies
- the entry points: CLI commands and their `--help`, a server's start command, the
  library's public API
- `SPEC.md`, `AGENTS.md`, `LICENSE`, `CONTRIBUTING`

Without read access of your own (an architect), send a `scout` for the entry points and
the public API.

## 2. Run every example
Every command and every output shown in the README must have been run. Run install, start
and usage examples in a temporary folder, never against real user data or services.
Without a shell of your own, give a `tester` task the exact list of commands to run and
ask for their output verbatim. Leave out anything that could not be run, or mark it
clearly as not checked.

## 3. Write it
Use these sections, dropping any that don't apply:

1. **Name** and one line on what it does.
2. **About:** 2-4 sentences. Who it is for and what problem it solves.
3. **Requirements:** language version, OS, services.
4. **Install:** exact commands.
5. **Usage:** 2-5 copy-pasteable examples with their real output, the common case first.
6. **Configuration:** options, environment variables, files.
7. **Development:** how to run the tests. Point to `AGENTS.md` for the details agents use.
8. **More:** links to the spec and other docs, plus license and contributing if they exist.

Rules:
- Describe only what exists today. No planned features, no placeholders, no badges or
  marketing.
- Link to the spec instead of repeating its rules.
- Keep the examples small; one good example beats a list of flags.

## 4. Write the file
- With `write`/`edit`: write it, then read it back.
- **Without file tools (an architect):** give one `coder` task with the full text:
  "Write exactly this content to README.md, change nothing else". Then `read` it back.

## 5. Report
List the sections written, which examples were run (and their command), and anything
left out because it could not be checked.

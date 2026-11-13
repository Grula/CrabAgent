---
name: skill-creator
description: Create, edit or improve a CrabAgent skill (a SKILL.md folder loaded by the skill tool). Use when the user says "make a skill", "turn this workflow into a skill", "save this as a skill", wants a skill's triggering fixed, or asks how CrabAgent skills work.
---

# Skill Creator

A skill is a folder with a `SKILL.md` file that CrabAgent offers through the `skill` tool. Only the
`name` and `description` sit in the tool's catalog all the time; the body is loaded when the `skill`
tool is called with that name, and extra files are listed so you can `read` them on demand.

Your job is to work out where the user is in the process (idea, draft, or "it doesn't trigger") and
move them forward. If they only want a quick draft, skip the testing ceremony.

## 1. Decide whether a skill is the right container

- **Skill**: reusable know-how — a workflow, checklist, house style, domain rules, a way of using
  existing tools. Write one when the same instructions would otherwise be retyped across sessions.
- **Custom tool** (`create_tool`): deterministic code the agent should *call* — parsing a format,
  hitting an API, a calculation that must be exact. It is a Python file in `.crabagent/tools/` with a
  `def run(...) -> str`. Prefer a tool when the "instructions" would really be "write this script
  every time". A skill can tell the agent to use a custom tool.
- **Neither**: one-off facts about this project usually belong in the project's AGENTS.md, not a skill.

## 2. Capture intent

If the conversation already contains the workflow ("turn this into a skill"), mine it first: the
tools used, the order of steps, corrections the user made, input and output formats. Then confirm or
ask only about the gaps:

1. What should the agent be able to do with this skill?
2. When should it trigger — what would a user actually type?
3. What does a good output look like (file, report format, code change)?
4. Any edge cases, required dependencies, or things it must never do?

## 3. Lay out the folder

```
<skill-name>/
├── SKILL.md            required: frontmatter + instructions
└── references/         optional: detail loaded only when needed
    ├── topic-a.md
    └── topic-b.md
```

Scripts or templates may also live in the folder (e.g. `scripts/check.py`, `assets/template.html`);
the agent runs them with `bash` or copies them with `read`/`write`.

**Where to save it**

| Location | Scope |
|---|---|
| `<workspace>/.crabagent/skills/<name>/` | This project only (default choice). |
| `~/.crabagent/skills/<name>/` | Every workspace for this user. |
| `<workspace>/.opencode/skills/`, `~/.opencode/skills/` | Also scanned unless disabled in settings. |

Directories are scanned in that order (project first, then home); when two skills share a `name`,
the **last one found wins**, so a home-level skill silently shadows a project one with the same name.
Pick a unique name. Bundled skills are copied into the workspace `.crabagent/skills/` the first time
that directory is created; edit the workspace copy, not the package.

## 4. Frontmatter — the loader is strict

The loader is a simple line parser, not a YAML library. Follow these rules exactly:

```markdown
---
name: my-skill-name
description: What it does in one sentence. Use when the user asks to X, mentions Y, or wants Z.
---
```

- The file must begin with `---` on line 1 and the block must close with `---` followed by a newline.
- `name`: lowercase letters, digits and single hyphens (`^[a-z0-9]+(-[a-z0-9]+)*$`), at most 64
  characters, and equal to the folder name. Invalid names make the skill disappear without an error.
- `description`: **one physical line**. YAML folding (`>` or `|`) or a continuation line leaves only
  the first fragment. Outer quotes are stripped; anything past 1024 characters is cut — aim for under
  ~300.
- No other key may start with `name:` or `description:` (even indented) — it would overwrite the
  real value. Other keys are ignored, so don't rely on them.
- At most 10 auxiliary files are listed to the agent; extra files exist but are never mentioned.
  Stray files (`.DS_Store`, `__pycache__/`) count against that limit — delete them.

## 5. Write a description that triggers

The description is the only thing the agent sees when deciding whether to load the skill, so all
"when to use" information goes here, not in the body.

- State the capability, then concrete triggers: user phrases, file types, situations.
- Agents tend to *under*-use skills, so be a little pushy: "Use whenever the user mentions
  dashboards or wants to chart company metrics, even if they don't say 'dashboard'."
- Add an exclusion only when it prevents a likely mis-route ("Not for Google Sheets").
- Simple one-step requests rarely trigger any skill; the description earns its keep on
  multi-step or specialized work.

## 6. Write the body

- Imperative voice, written for another capable agent. Include only what changes its decisions:
  the non-obvious steps, real constraints, house conventions, output format.
- Explain *why* behind rules instead of stacking ALL-CAPS MUSTs; a model that understands the reason
  handles cases you didn't foresee.
- Name CrabAgent's real tools (`read`, `write`, `edit`, `glob`, `grep`, `bash`, `web_search`,
  `web_scrape`, `browser_*`, `collab_browser_*`, `image_generate`, custom tools) — never tools from
  other products.
- Show the output shape with a short template or an input→output example when format matters.
- Keep SKILL.md roughly 60–200 lines. Move bulky material (schemas, API notes, long examples,
  per-variant guides) into `references/` and say *when* to read each file, e.g.
  "For AWS deployments read the aws reference file." Keep each fact in one place.
- No malware, credential harvesting, or instructions whose intent would surprise the user.

See `references/writing-guide.md` for templates, a worked example, and the improvement loop.

## 7. Validate

Run the bundled checker; it applies the same rules as CrabAgent's loader:

```bash
python <this-skill-dir>/scripts/validate_skill.py <path/to/skill-folder>
```

Fix every reported error. A skill that fails validation is silently skipped at load time.

## 8. Test it

1. Skills are rediscovered on every new message, so send a new message (or start a new session).
2. Call the `skill` tool with the new name; confirm the body loads and auxiliary files are listed.
3. Try 2–3 realistic prompts a user would type, in a fresh session, and watch whether the agent
   picks the skill on its own. Also try 2–3 near-miss prompts that share keywords but should not
   trigger it.
4. Read the transcript, not just the final answer. If the agent wasted steps, cut or clarify the
   part of the skill that led there. If every run hand-wrote the same helper script, bundle it.
5. Show the user the results and iterate until they are happy or changes stop helping.

## Updating an existing skill

Keep its folder name and `name` unchanged (renaming breaks anyone who loads it by name). Make the
smallest edit that fixes the observed problem, re-run the validator, and re-test the prompts that
motivated the change.

<!-- Adapted from Anthropic claude-code/skills/skill-creator and Meta muse-code/skills/create-skill (Apache-2.0). Modified for CrabAgent. -->

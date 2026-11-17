---
name: deep-research
description: Multi-source web research that ends in a cited report with confidence levels. Use when the user asks to research, investigate, compare options, survey a market or literature, or wants a "deep dive" / "full report" / "what do sources say" answer.
---

# Deep Research

You run the research as a coordinator: frame the question, send researchers after
well-scoped sub-questions in parallel, check the claims that matter, and write one
report where every important fact traces back to a source.

**Skip this skill** for a single fact ("when was X released?"). One or two
`web_search` calls and a direct answer are better there.

## What the tools actually do (plan around this)

| Mechanism | Behaviour that matters here |
|---|---|
| `researcher` agent | Has `web_search`, `web_scrape`, browser tools. **Cannot write files** or run commands. Returns its findings as its reply. |
| `coder`, `tester`, `the_reviewer` | No web access. Use them only on material you already gathered (`coder` can save files). |
| `delegate_parallel(tasks)` | All tasks start together. You receive every reply in full. This is the main fan-out tool. |
| `run_pipeline(steps)` | A dependent step sees only about the **first 1500 characters** of each dependency's result. Don't use a research → write pipeline for long notes. |
| `shared_put(key, value)` | Each note (max 10,000 chars) is **injected into every sub-agent spawned later in this session**. Use it for short shared context, like the scope or a glossary. Never put raw notes there. |
| Background agents | Get **no web tools** and have a 5-minute limit. They can't gather sources, so don't use them for this skill. |

Check your Agent Team list: any agent with `web_search` (for example a domain
researcher) can take the `researcher` role, and may suit the topic better.

## Process

### 1. Frame the question
- Restate the question in one sentence and list the constraints: time window,
  region or jurisdiction, audience, and the decision the answer supports.
- Ask follow-up questions **only** if the answer would change what you search for,
  such as an ambiguous acronym, a missing region or an unclear entity. Ask at most 3
  (with `ask_question` when you have it, otherwise in your reply), then wait.
  Otherwise state your assumptions and continue.
- Note today's date. "Current" means as of that date.

### 2. Decompose
Split the question into sub-questions that don't overlap and together cover the whole
question. Size the team to the question:

| Shape of question | Researchers |
|---|---|
| Narrow factual or technical, with a few angles | 2–3 |
| Comparison across criteria (price, features, risk…) | one per criterion, 3–5 |
| Many entities, regions or products | one per entity or cluster, sent in waves of at most 6 |

Add one **skeptic** researcher whenever the topic is contested. Its job is to find the
strongest evidence against the likely conclusion.

### 3. Fan out
Send one `delegate_parallel` call with a self-contained brief per researcher. Build
each brief from [references/researcher-brief.md](references/researcher-brief.md);
researchers that have the `web-research` skill already follow its method. A
brief must carry the user's constraints, because researchers never see the
conversation. For more than 6 sub-questions, run waves of up to 6 and finish each
wave before starting the next.

### 4. Build the claim ledger and cross-check
From the replies, write down every load-bearing claim. A claim is load-bearing if
removing it would change the conclusion. Record each one with its sources and the
date of each source. Then mark each claim:

- **Corroborated**: two independent sources agree, or one primary source states it
  (a filing, the official docs, the paper itself, the original dataset).
- **Single-source**: only one secondary source supports it.
- **Contested**: credible sources disagree. Keep both sides and the reason they differ.
- **Unsupported**: no URL backs it. Drop it from the report or list it as a gap.

If a load-bearing claim is single-source or contested and you can verify it, run
**one** targeted round of verification. Send `delegate_parallel` to researchers with
narrow tasks such as "Find the primary source for <claim>. Report whether it holds,
with the URL and date." Allow only one extra round, then write the report with what
you have.

### 5. Write the report
Follow [references/output-format.md](references/output-format.md). Write it
yourself, because you hold the full notes. Never hand the writing to a pipeline step:
a dependency excerpt would truncate the claim ledger.

### 6. Deliver
- If the report runs longer than about 60 lines, save it to
  `reports/<short-title>.md` in the workspace and give the path. If you can't write
  files, send the finished report in one `coder` task that saves it unchanged.
- In your reply, give the key answer in 3–5 sentences, the overall confidence, the
  biggest open question, and where the full report is.

## Citation discipline
- Every number, date, quote and specific factual claim gets an inline link
  `[source title](URL)`. If you have no URL, drop the claim.
- Prefer primary sources over aggregators. A blog post about a study is a pointer
  to the study, not evidence on its own.
- Date sensitive facts, for example "as of the Q2 2026 filing". Don't present stale
  figures as current.
- Keep what sources say separate from what you infer. Label inferences as inferences.
- When sources conflict, show the conflict and explain which side you weight more and
  why. Never average conflicting values or pick one silently.
- Never invent a source, statistic or quote. Say "no reliable source found" instead.

## Without sub-agents
If delegation is unavailable, or the question is mid-sized, run the same loop
yourself:
1. Run 4–10 varied `web_search` queries, mostly short and specific, from different
   angles.
2. Use `web_scrape` on the most promising primary pages.
3. Keep the claim ledger as you go.
4. Stop when new queries stop turning up new facts.

---
name: web-research
description: Answer a research or documentation question from the web, using sources you actually read - a quick lookup (one fact, an API's documented behaviour, a version) or a broader question - and report the answer with URLs, dates and confidence. Use when you are given a question to research.
---

# Web Research

You answer one question, for someone who sees only your reply. Read sources, don't
guess, and stop when the question is answered.

## 1. Size the work
- **A lookup** (one fact, what a function does, which version added something): go
  to the authoritative page, read the part that answers it, and quote it with the
  URL. A few tool calls.
- **A question** (compare options, "what do sources say", a survey): follow the
  steps below, within about 15 tool calls. If it is bigger than that, answer what you
  can and list the rest under GAPS.

If the brief names a source or a version (cppreference, C++20, Python 3.12), use
exactly that.

## 2. Search and read
- Use short, varied queries, and don't repeat one. Add the version or year when it
  matters.
- Search results only point the way. Open the page you rely on (`web_scrape`, or the
  browser for pages that need JavaScript) and read the relevant part; snippets are
  easy to misread.
- Prefer primary sources: official docs and standards, the project's repository and
  changelog, the paper, the filing, the dataset. Tutorials, blogs and Q&A answers can
  lead you to a source; they don't settle a claim.
- If a page is paywalled or won't load, record it under GAPS. Don't guess what it
  says.

## 3. Check before you claim
- A claim the answer depends on needs one primary source, or two independent
  secondary sources that agree.
- Give the date of anything that changes over time ("as of the 2026-09 release");
  "current" means as of today.
- When sources disagree, report both sides and why they differ. Don't average them
  or silently pick one.
- Never invent a URL, version, number or quote. Write "not found" instead.

## 4. Reply
Lead with the answer, keep to what was asked, and use this shape:

```
ANSWER: <1-3 sentences>
EVIDENCE:
- <claim> - [source title](URL), <date> - "<short quote>" when the exact wording matters
CONFIDENCE: high | medium | low - <why>
GAPS: <what you couldn't find or read, or "none">
```

For a documentation question from a teammate, quote the signature and the sentence
that answers it.

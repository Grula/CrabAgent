# Researcher brief template

Fill this in once for each researcher and pass it as the `task` in
`delegate_parallel`. The researcher sees only this text and any notes in the shared
workspace, so include everything it needs.

```
Research sub-question: <one narrow question>

Why it matters: <one sentence linking it to the user's overall question>

Answer these:
1. <specific question>
2. <specific question>
3. <optional>

Constraints: <time window, region, audience, units, "as of <today's date>">
Out of scope (other researchers cover these): <adjacent topics to skip>

Where to look: <kinds of primary sources, e.g. official docs, regulator sites,
company filings, peer-reviewed papers, standards bodies, maintainers' changelogs>

Method:
- Search with short, varied queries. Never repeat the same query.
- Scrape the pages you rely on. Search snippets are easy to misread.
- Prefer the original source over coverage of it.
- Stop when you have answered the questions with sourced facts, when new searches
  stop adding anything, or after about 15 tool calls.

Reply in exactly this format:

## Answer
<2-4 sentences answering the sub-question>

## Findings
- <fact or figure> | <source title> | <URL> | <publication or data date> | primary/secondary
- ...

## Conflicts
- <claim A [URL]> vs <claim B [URL]>: <likely reason they differ>

## Gaps
- <what you could not find or verify, and where you looked>

Rules: Every finding needs a URL. A fact with no URL goes under Gaps. Never invent
numbers, quotes or sources. Mark predictions and opinions as such.
```

## Skeptic variant
Replace "Answer these" with:

```
The emerging view is: <likely conclusion>.
Find the strongest credible evidence AGAINST it: failures, counter-studies,
critiques, regulatory problems, cases where it did not hold. Report how strong the
counter-evidence is, not just whether it exists.
```

## Verification variant (step 4 follow-up)

```
Verify this claim: "<claim>" (currently sourced to <URL>).
Find the primary source. Report HOLDS / DOES NOT HOLD / PARTLY, with the exact
supporting text, its URL and its date. If newer data supersedes it, give the newer
figure.
```

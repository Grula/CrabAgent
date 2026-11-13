# Output format for the research deliverable

Match the length to the question. An engineering decision needs a tight document that
supports the choice. A broad survey can run longer. Usually 3–5 body sections are
enough.

```markdown
# <Specific title that states the finding>

*Researched <date>. Scope: <constraints and assumptions>.*

## Bottom line
<One paragraph that answers the question directly in its first sentence, then gives
why it matters and the main caveat. A reader who stops here should still be able to
act.> **Overall confidence: High / Medium / Low** because <one clause>.

## <Header that states an insight, not a topic>
<Prose with the key figures in **bold**. Cite inline: [Source title](URL). Each
section should move the argument forward, not catalogue facts. Use a table only for a
comparison across the same attributes.>

## <Next insight>
...

## Where the evidence disagrees
<Each important conflict: what each side says, with citations, and which side you
weight more and why. Leave this section out only if there was no real conflict.>

## What we don't know
<Gaps, single-source claims, and data older than the question needs.>

## Implications
<What follows from the findings for the user's decision. Don't restate the sections
above.>

## Sources
1. [Title](URL), publisher, date. Note primary or secondary.
2. ...
```

## Confidence labels
Label the overall answer and any claim the reader might act on.

| Label | Use when |
|---|---|
| **High** | A primary source, or several independent sources agree, and the data is current. |
| **Medium** | Credible but secondary sourcing, some extrapolation, or minor conflicts. |
| **Low** | A single weak source, heavy inference, stale data, or unresolved conflicts. |

## Headers
Good headers state the finding, e.g. "Licensing costs doubled after the 2025 price
change". Weak headers only name the topic, e.g. "Pricing". Someone who reads only the
headers should come away with the story.

## Final checks before delivery
- Every number has a link and a date.
- No claim in the deliverable is missing from the claim ledger.
- Inferences are worded as inferences ("this suggests…").
- The first sentence of "Bottom line" answers the user's actual question.

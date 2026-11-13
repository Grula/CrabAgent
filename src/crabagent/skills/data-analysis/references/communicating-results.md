# Communicating results

Skill reference: how the agent should present analysis results to the user. Answer
first, evidence second, caveats where they matter, next steps last.

## Structure

1. **Headline answer** - a full sentence with the key number and its comparison.
   "Q3 churn rose to 3.1% (from 2.4%), driven by annual-plan renewals in the SMB segment."
   Not "Churn analysis" or "Key insights".
2. **Supporting findings** - 2-5, each a complete-sentence heading followed by the
   evidence (a small table or chart and 1-3 sentences explaining what it shows and why it
   matters).
3. **What is less certain** - hypotheses not yet tested, with the evidence for and
   against and the check that would settle each.
4. **Recommendations / next steps** - specific actions tied to findings ("Contact the 42
   SMB accounts with renewals in the next 30 days and a support ticket open"), or the
   analysis/experiment needed to decide.
5. **Method and data** - sources, time window, definitions, filters, cleaning decisions,
   known data issues, where the code/workbook lives.

For short answers in chat, compress to: answer (1-2 sentences) -> 2-4 bullets of
evidence -> one line of caveats -> next step.

## Writing numbers

- Every number gets context: comparison, denominator, period. "Signups: 3,380 (-18% WoW)".
- Rates with sample sizes when small: "40% (8 of 20)".
- Consistent precision: 1 decimal for percentages, sensible rounding for money ($4.4M, not
  $4,431,227.53, unless exactness matters).
- Percentage points for differences between rates ("rose 0.7 pts to 3.1%"), percent for
  relative change.
- Say "about" or give a range when the estimate is uncertain; show intervals when available.

## Language

- Plain words, concrete subjects, active voice: "Customers who used templates in week 1
  renewed more often" beats "Template engagement is positively associated with renewal
  propensity".
- Separate what the data shows from interpretation: "Usage dropped 30% the week the new
  login flow shipped; the flow is the likely cause, but it hasn't been compared against a
  holdout."
- Don't over-hedge. State each relevant caveat once, beside the claim it affects. Drop
  caveats that don't change the reader's interpretation.
- Don't turn correlations or contribution breakdowns into causal headlines.
- Name segments, products and behaviors, not jargon ("activation opportunity").
- Distinguish a recommendation from a decision someone has already made.

## Tables and charts

- Each one has a title stating its point and a note with source/period.
- Tables: sorted by what matters, totals row where additive, numbers right-aligned,
  units in headers, no more rows than needed (put the full table in an appendix/workbook).
- Charts: follow the data-visualization skill; every chart supports a specific sentence.

## Before delivering

- Re-run the analysis end to end from raw data; numbers in text, tables and charts agree.
- Check every claim against the evidence shown; remove anything that can't be supported.
- Read the headline and first paragraph alone - do they answer the question?
- List what was not checked, and any assumptions the reader must know.

## Outlines

**Metric movement**
> **[Metric] [rose/fell] [X%] [period-over-period] to [value], mainly because [segment/driver].**
> Size and timing | Data checks (complete periods, no tracking changes) | Breakdown table
> | Likely explanation (confidence) | Other possibilities ruled in/out | Next step

**Opportunity sizing**
> **[Opportunity] could add about [range] per [period].**
> Baseline | Assumptions (each with source) | Calculation | Sensitivity (low/base/high)
> | Risks | What would confirm it

**Data-quality review**
> **[Dataset] is [fit / fit with caveats / not fit] for [use].**
> Grain and coverage | Checks run | Issues (evidence, impact, severity) | Fixes and tests
> | Open questions

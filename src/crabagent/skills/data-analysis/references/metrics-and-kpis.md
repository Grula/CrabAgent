# Metrics and KPIs

## Define every metric explicitly

| Field | Example |
|---|---|
| Name | Weekly active customers |
| Question it answers | Is the product becoming part of customers' weekly routine? |
| Definition | Distinct customer accounts with >= 1 qualifying action in the ISO week |
| Numerator / denominator (for rates) | Retained = customers active in week 4 / customers who signed up in week 0 |
| Unit and grain | count of accounts per week |
| Filters / exclusions | exclude internal + test accounts; exclude trials < 24h |
| Time zone & window | UTC; complete weeks only |
| Source | `events` table, `customers` dimension |
| Owner / refresh | Product analytics, daily |
| Known caveats | tracking for the Android app started 2026-03-02 |

Small wording differences ("users" vs. "accounts", "signed up" vs. "activated") change
numbers a lot - put the definition next to the number whenever it is reported.

## Choosing KPIs

Generate candidates first, then narrow. For each goal, consider three kinds:
- **Outcome** - the result you ultimately want (retained revenue, activated customers,
  on-time deliveries). Slow-moving, hard to influence directly.
- **Driver** - inputs the team can move that should lead to the outcome (setup completion
  rate, weekly usage of the core feature, pick accuracy). Faster signal.
- **Guardrail** - what must not get worse while optimizing (refund rate, latency, support
  tickets per order, unsubscribe rate).

A good KPI:
- reflects the goal (if it's a proxy, say why it should track real progress and where it
  could mislead);
- would change a decision when it moves;
- moves at the cadence it is reviewed (weekly reviews need weekly-moving signals);
- can be influenced by the team that owns it;
- can be measured consistently without manual work;
- is hard to game - improving it shouldn't be possible by hurting something unmeasured
  (pair it with a guardrail).

Recommend 1-3 primary KPIs, 1-2 drivers each, and 1-2 guardrails. More metrics dilute
attention.

## Typical shapes

| Context | Outcome | Drivers | Guardrails |
|---|---|---|---|
| Product launch | adopting accounts using it weekly after 4 weeks | activation rate, time to first value | errors, support contacts, overall engagement |
| Growth / acquisition | new paying customers | traffic by channel, visit->signup, signup->paid | CAC, early churn |
| Retention | net revenue retention | active usage, seats used/purchased | discount rate, support backlog |
| Operations | on-time fulfilment rate | throughput per shift, backlog age | error/return rate, overtime hours |
| Experiment | one pre-registered primary metric | diagnostic metrics explaining movement | guardrails with thresholds |

## Rates, ratios and averages

- Report rates with their denominator and window: "12.4% of 3,210 trial starts converted
  within 14 days".
- Aggregate rates as total numerator / total denominator.
- Cohort metrics need mature cohorts - don't compare a 2-week-old cohort's 30-day retention.
- Averages of skewed values hide the story - add median/p90.
- Per-user metrics: be explicit whether users with zero activity are in the denominator.

## Setting targets

Treat target-setting as separate from choosing the metric.
- **Top-down:** benchmarks, historical growth, what the business case needs.
- **Bottom-up:** what planned work can plausibly move (reach x expected effect).
Compare the two; a good target is meaningful and achievable. State the anchor,
assumptions and confidence. If the baseline is unknown, the first target is "establish
a reliable baseline by <date>".

## KPI report / dashboard checklist

- Each KPI: current value, comparison (previous period and/or target), trend, definition
  on hover or in a footnote.
- Same definitions and time windows across all panels; partial periods flagged.
- Drivers placed next to the KPI they explain; guardrails visible, not hidden on page 3.
- Thresholds or targets drawn on charts when they exist.

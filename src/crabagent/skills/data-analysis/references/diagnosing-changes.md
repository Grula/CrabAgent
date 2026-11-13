# Diagnosing a metric change

"Why did X go up/down?" Work from size to cause - never start with a favorite theory.

## 1. Pin down the change

- Reproduce the number from source with the official definition. If your number differs
  from the dashboard the user saw, reconcile first (filters, time zone, refresh time).
- Size it: absolute and relative change, vs. which baseline (previous period, same period
  last year, forecast). Is it outside normal variation? Compare with the last 8-12 periods'
  range or a seasonal baseline.
- Timing: when did it start (day/hour), is it a step, a trend, or a spike that recovered?
- Data checks: complete period? tracking/definition change? pipeline delay? (see
  data-quality.md). A large share of "metric drops" are data problems.

## 2. Decompose

**Volume x rate.** For a metric like revenue = orders x average order value, or
conversions = visitors x conversion rate:
```
delta_total ~= delta_volume * old_rate  +  old_volume * delta_rate  +  delta_volume * delta_rate
```
**Mix vs. within-segment (for rates).** Overall rate = sum(share_i x rate_i). Change splits
into:
- *mix effect* = sum((share_new_i - share_old_i) x rate_old_i) - traffic moved to segments
  with different rates;
- *rate effect* = sum(share_new_i x (rate_new_i - rate_old_i)) - segments themselves
  changed.

```python
import pandas as pd

def mix_rate_decomposition(old: pd.DataFrame, new: pd.DataFrame, seg: str, num: str, den: str):
    """old/new: rows per segment with numerator and denominator columns."""
    o = old.groupby(seg)[[num, den]].sum(); n = new.groupby(seg)[[num, den]].sum()
    t = o.add_suffix("_old").join(n.add_suffix("_new"), how="outer").fillna(0)
    t["share_old"] = t[f"{den}_old"] / t[f"{den}_old"].sum()
    t["share_new"] = t[f"{den}_new"] / t[f"{den}_new"].sum()
    t["rate_old"] = (t[f"{num}_old"] / t[f"{den}_old"]).fillna(0)
    t["rate_new"] = (t[f"{num}_new"] / t[f"{den}_new"]).fillna(0)
    t["mix_effect"] = (t["share_new"] - t["share_old"]) * t["rate_old"]
    t["rate_effect"] = t["share_new"] * (t["rate_new"] - t["rate_old"])
    total_old = t[f"{num}_old"].sum() / t[f"{den}_old"].sum()
    total_new = t[f"{num}_new"].sum() / t[f"{den}_new"].sum()
    assert abs((total_new - total_old) - (t["mix_effect"].sum() + t["rate_effect"].sum())) < 1e-9
    return t.sort_values("rate_effect"), total_old, total_new
```

**Contribution by segment (additive metrics).** contribution_i = value_new_i - value_old_i;
share of change = contribution_i / total change. Rank segments by contribution, and also
look at their relative change - a small segment can move a lot without mattering.

**Funnels.** Break the end metric into stage counts and stage conversion rates; find the
first stage where the change appears.

## 3. Choose the cuts

Pick dimensions from how the business works and what the team can act on: channel,
geography, platform/app version, customer segment/plan, cohort (signup month), product,
new vs. returning. Rebuild business groupings if the table only has raw codes. Stop
cutting when further splits don't change the explanation.

Check whether the change is **broad** (all segments move similarly - points to a shared
cause: seasonality, pricing, macro, a site-wide bug) or **concentrated** (one segment
explains most of it - points to something specific to it).

## 4. Explain - carefully

- Arithmetic is not causation. "Paid search explains 80% of the drop" tells you *where*,
  not *why*. The why needs a mechanism plus evidence (timing matches a change, the
  affected segment is the one exposed, an unaffected comparison group stayed flat).
- Line up known events: releases, pricing changes, campaigns starting/ending, outages,
  holidays, tracking changes, competitor actions. Timing alignment is suggestive, not proof.
- Consider reverse causality and confounders (a feature used by engaged users correlates
  with retention without causing it).
- Prefer experiments or natural experiments (staggered rollouts, unaffected regions) for
  causal claims; otherwise label conclusions as likely/possible and say what would confirm
  them.

## 5. Output

1. The change, sized and timed, with its baseline and whether it is unusual.
2. Data-quality confirmation (or the issue found).
3. Decomposition table (segment, before, after, contribution, % of change).
4. The leading explanation(s) with supporting and contradicting evidence, labeled by
   confidence.
5. What to do next: an action if confident, or the specific check/experiment that would
   resolve the uncertainty.

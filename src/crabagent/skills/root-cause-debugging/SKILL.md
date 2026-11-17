---
name: root-cause-debugging
description: Find and prove the root cause of a bug before changing any code (reproduce, trace, test hypotheses, ask why until you reach the real cause), then apply the smallest fix that removes it. Use when a test fails, an error or traceback is reported, or behavior is wrong and the cause is not obvious.
allowed-tools: bash read grep glob edit
---

# Root-Cause Debugging

Don't guess at fixes. A fix you can't explain is a coincidence, and the bug will come back. Work like an investigator: observe, form a hypothesis, test it, and only then change the code.

## Step 1: Reproduce
- Run the failing test, command or request. Capture the **exact** error, traceback and output, and write down the command so you can rerun it.
- If it doesn't reproduce, find out what differs before going further: inputs, environment variables, data, versions, order of operations. You can't confirm a fix for a bug you can't trigger.
- Shrink the reproduction to the smallest input and the narrowest test that still fails.

## Step 2: Trace the failure to its origin
- Start at the line that raised the error: the innermost frame in *your* code, not in a library. Read the function around it.
- Then walk backwards through the data. Where did the bad value come from? Use `grep` to find where it is created, transformed and passed along, and read those places.
- Check what changed recently: `git log -p -n 5 -- <file>`, or `git diff` against a version that worked. If you have a known-good commit and a cheap reproduction, use `git bisect run <repro command>`.

## Step 3: Test hypotheses, one at a time
- State a hypothesis that can be proven wrong. Example: "`total` is None because `load_cart()` returns None when the session has expired."
- Test it with the cheapest probe: a targeted assertion, a one-off script, or a temporary log line that prints the actual value. Change one thing at a time.
- If the result contradicts the hypothesis, drop it; don't bend the evidence to fit. After two failed hypotheses, re-read the error and the code from scratch.
- Remove every temporary probe when you're done.

## Step 4: Ask why until you reach the cause
Keep asking "why?", usually about five times, until the answer points at a decision in the code rather than a symptom:

> Why did the request crash? `user` was None.
> Why? The lookup returned nothing.
> Why? It queries by lowercased email, but sign-up stored the address as typed.
> Why? Only the sign-in path normalizes email.
> **Root cause: email normalization is missing at sign-up.**

The root cause is where the correct fix belongs, and it is often far from where the error appeared.

## Step 5: Fix the cause, minimally
If your role or brief says not to change code (a tester or reviewer), stop here: report the root cause, the evidence and where the fix belongs.
- State the root cause in one sentence before you edit anything.
- Make the smallest change that removes the cause.
- Don't patch the symptom (for example, a None check where the crash happened) unless the cause truly can't be fixed. If so, say that explicitly.
- No refactoring or unrelated cleanup in the same change.

## Step 6: Prove it
1. Rerun the original reproduction. It must pass now.
2. Add a regression test that fails without the fix (see `proportional-testing`).
3. Run the surrounding test file or suite to check for side effects.

## Report
- **Root cause:** one sentence.
- **Evidence:** the probe or observation that confirmed it.
- **Fix:** files and lines changed.
- **Verification:** the commands you ran and their results.
- **Related risks:** other places with the same pattern, mentioned but not fixed.

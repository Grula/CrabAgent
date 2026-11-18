---
name: bug-triage
description: Coordinate a bug fix through the team - reproduce it the way the user hit it, find the cause before anyone fixes it, verify on that same path, and handle "still not working" without stacking fixes. Use when the user reports a bug, pastes an error, log or screenshot, or says a fix didn't work.
---

# Bug Triage

You coordinate; `tester` and `coder` do the hands-on work. Two rules carry
this skill:

1. **Nobody fixes what nobody has reproduced.**
2. **Nothing is fixed until it works on the path the user used.**

Brief templates for every step below come with the `parallel-workflows` skill
(its brief templates file). How to build, start and check the project is in the
`run-app` skill.

## 1. Pin down the symptom
From the user's message, write down:
- **Their path:** exactly where it happens. "The browser panel's Open button with
  `localhost:5173/x`", not "the browser". The page and control, the command, the
  API call, or the agent action.
- **Observed vs expected**, in their words.
- **Their evidence:** error text, log lines, screenshot. Quote it; it is the
  strongest clue you have.
- **What changed** recently, if they said.

If you can't tell their path, ask one question (see `grill`, quick mode).
Otherwise go on.

## 2. Reproduce it on their path
Send `tester` to reproduce it on that exact path in the running program, and to
capture the failing output.
- A unit test written for your hypothesis is **not** a reproduction. It shows the
  hypothesis is true, not that it is the user's bug.
- If it doesn't reproduce, find what differs before going further: a stale build,
  a server that wasn't restarted, a different input, port, setting or user. Ask
  the user for the missing detail rather than guessing.

## 3. Find the cause
Read the code along the failing path yourself (`grep`, `read`), or send a
read-only investigation to `scout`.
- Test one hypothesis at a time, each with a probe that could prove it wrong.
- You have the cause when you can state it in one sentence, point at the line,
  and it explains **every** symptom in the report. A cause that explains only part
  of it is not the cause; two problems can stack (for example a refused request
  plus an error message that hides the reason).

## 4. Fix the cause
Pipeline: `tester` writes a regression test that fails because of the root cause
-> `coder` fixes the cause with the smallest change -> `the_reviewer`.
Give the coder the root-cause sentence and the evidence. Reject fixes that
make the symptom disappear without explaining it: retries, take-overs, wider
permissions, swallowed errors.

## 5. Verify on their path
Once the change is live (rebuilt, copied, restarted as the project needs), send
`tester` to repeat the step 2 reproduction and one neighbouring check. Only that
output counts as "fixed".

Your own tools are a different path. Opening a page with the agent's browser tools
does not show that the user's browser panel works, unless the agent's tools are
what the user used.

## 6. Report
- **Root cause:** one sentence.
- **Fix:** what changed.
- **Evidence:** the reproduction now passing on their path.
- **What the user must do** to get it: restart, rebuild, hard reload.
- **Not checked:** anything you could not verify.

## When the user says "still not working"
Don't add another fix on top. In this order:
1. **Is the fix running?** The usual answer is no: stale build, files not copied,
   server not restarted, browser cache. Tell the user exactly what to restart.
2. **Reproduce again** on their path with their new evidence. Ask for the current
   error, log or screenshot if they didn't send one.
3. **Still failing:** your cause was wrong or incomplete. Say so plainly and go
   back to step 3 with the new evidence.

After two rounds that fail, stop. Tell the user what you know, what you have ruled
out, and what evidence you need from them.

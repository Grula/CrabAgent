Check two things in the running CrabAgent web UI, in a real browser. This workspace is a copy of the CrabAgent repository with its UI already built; AGENTS.md and the run-app skill say how to check the UI safely. Don't change any code.

1. On the start screen and in an empty chat, the "Continue a plan" row lists the workspace's `docs/*plan*.md` files by title. A plan whose title is exactly 40 characters long must be shown whole; a longer title is shortened to 40 characters ending in "…".
2. On the Agents page, an agent's "Compact context above (tokens)" field accepts `200k`: saving succeeds, and the field shows `200000` when the agent is opened for editing again.

Start your reply with exactly two lines, `ITEM 1: PASS` or `ITEM 1: FAIL`, and `ITEM 2: PASS` or `ITEM 2: FAIL`, then say what you saw for each.

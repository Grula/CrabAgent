In CrabAgent (this workspace is a copy of its repository; AGENTS.md has the commands), rename the method `ToolRegistry.get` in `src/crabagent/core/agent/tools/registry.py` to `get_tool`, with the same behaviour, and update every caller in `src/` and `tests/`. Don't keep a `get` alias.

Only calls on a `ToolRegistry` change: `.get(` on dicts and any other object must stay exactly as it is. Keep the Python test suite passing.

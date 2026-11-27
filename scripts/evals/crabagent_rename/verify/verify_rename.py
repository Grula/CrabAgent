"""Hidden check for crabagent_rename: every ToolRegistry.get call renamed, nothing else.

Counts call expressions with ast in the files that existed before the agent started
(new files the agent adds don't count either way). The ground truth was measured on
the starting point: 47 calls on a ToolRegistry (38 typed + 9 on untyped receivers such
as `context.tool_registry`) among 2002 `.get(` calls in src/ and tests/.
"""

import ast
import subprocess
import sys
from pathlib import Path

EXPECTED_RENAMED = 47
ORIGINAL_GET_CALLS = 2002

sys.path.insert(0, "src")
from crabagent.core.agent.tools.registry import ToolRegistry  # noqa: E402

errors = []
if hasattr(ToolRegistry, "get"):
    errors.append("ToolRegistry still has a `get` attribute (alias or not renamed)")
if not callable(getattr(ToolRegistry, "get_tool", None)):
    errors.append("ToolRegistry.get_tool is missing")
else:
    registry = ToolRegistry()

    @registry.register(name="probe", description="probe", parameters={"type": "object", "properties": {}})
    def _probe() -> str:
        return "ok"

    if registry.get_tool("probe") is None or registry.get_tool("missing") is not None:
        errors.append("get_tool doesn't behave like the old get")

tracked = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD", "--", "src", "tests"],
                         capture_output=True, text=True, check=True).stdout.split()
get_calls = get_tool_calls = 0
for rel in tracked:
    path = Path(rel)
    if path.suffix != ".py" or not path.exists():
        continue
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        errors.append(f"{rel} no longer parses: {exc}")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            get_calls += node.func.attr == "get"
            get_tool_calls += node.func.attr == "get_tool"

if get_tool_calls != EXPECTED_RENAMED:
    errors.append(f"{get_tool_calls} .get_tool( calls in the existing files, expected {EXPECTED_RENAMED}")
if get_calls != ORIGINAL_GET_CALLS - EXPECTED_RENAMED:
    errors.append(f"{get_calls} other .get( calls left, expected {ORIGINAL_GET_CALLS - EXPECTED_RENAMED} "
                  "(a missed caller, or a dict/other .get renamed or removed)")
print("\n".join(errors) if errors else f"OK: {get_tool_calls} calls renamed, {get_calls} other .get calls untouched")
sys.exit(1 if errors else 0)

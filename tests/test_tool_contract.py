"""Tool-argument contract: coercion before validation, one actionable error string."""

from __future__ import annotations

import pytest

from crabagent.core.agent.tools.registry import ToolRegistry
from crabagent.core.agent.tools.schema_validation import coerce_args, validate_args

INVALID_PREFIX = "Error: invalid arguments for tool"


def _schema(**props) -> dict:
    return {"type": "object", "properties": props}


@pytest.mark.asyncio
async def test_valid_arguments_reach_handler_unchanged():
    registry = ToolRegistry()

    @registry.register(
        name="echo_tool",
        description="echo",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["name"],
        },
    )
    def echo_tool(name: str, count: int = 1, context=None):
        return f"{name}:{count}"

    assert await registry.execute("echo_tool", {"name": "x", "count": 3}, context=None) == "x:3"


@pytest.mark.asyncio
async def test_integer_string_is_coerced_before_handler():
    registry = ToolRegistry()
    seen: list[object] = []

    @registry.register(
        name="int_tool",
        description="int",
        parameters=_schema(count={"type": "integer"}),
    )
    def int_tool(count: int = 0, context=None):
        seen.append(count)
        return f"type:{type(count).__name__}"

    result = await registry.execute("int_tool", {"count": "5"}, context=None)

    assert result == "type:int"
    assert seen == [5]


@pytest.mark.asyncio
async def test_json_string_is_repaired_for_object_property():
    registry = ToolRegistry()
    seen: list[object] = []

    @registry.register(
        name="obj_tool",
        description="obj",
        parameters=_schema(config={"type": "object", "properties": {"depth": {"type": "integer"}}}),
    )
    def obj_tool(config: dict | None = None, context=None):
        seen.append(config)
        return "ok"

    result = await registry.execute("obj_tool", {"config": '{"depth": "2"}'}, context=None)

    assert result == "ok"
    assert seen == [{"depth": 2}]


@pytest.mark.asyncio
async def test_enum_violation_names_the_offending_argument():
    registry = ToolRegistry()
    called: list[str] = []

    @registry.register(
        name="mode_tool",
        description="mode",
        parameters=_schema(mode={"type": "string", "enum": ["fast", "slow"]}),
    )
    def mode_tool(mode: str = "fast", context=None):
        called.append(mode)
        return "ok"

    result = await registry.execute("mode_tool", {"mode": "sideways"}, context=None)

    assert result.startswith(f"{INVALID_PREFIX} 'mode_tool':")
    assert "mode" in result
    assert "sideways" in result
    assert result.endswith("Fix the arguments and call the tool again.")
    assert called == []


@pytest.mark.asyncio
async def test_wrong_scalar_type_reports_arg_path():
    registry = ToolRegistry()

    @registry.register(
        name="nested_tool",
        description="nested",
        parameters=_schema(
            options={"type": "object", "properties": {"retries": {"type": "integer"}}}
        ),
    )
    def nested_tool(options: dict | None = None, context=None):
        return "should not run"

    result = await registry.execute("nested_tool", {"options": {"retries": "abc"}}, context=None)

    assert result.startswith(f"{INVALID_PREFIX} 'nested_tool':")
    assert "options.retries" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters", [{}, None])
async def test_permissive_schema_accepts_anything(parameters):
    registry = ToolRegistry()

    @registry.register(name="free_tool", description="free", parameters=parameters)
    def free_tool(anything: object = None, context=None):
        return f"got:{type(anything).__name__}"

    assert await registry.execute("free_tool", {"anything": "text"}, context=None) == "got:str"


@pytest.mark.asyncio
async def test_undeclared_extra_arguments_are_still_dropped():
    registry = ToolRegistry()

    @registry.register(
        name="strict_tool",
        description="strict",
        parameters=_schema(value={"type": "string"}),
    )
    def strict_tool(value: str = "", context=None):
        return f"value={value}"

    result = await registry.execute(
        "strict_tool", {"value": "a", "invented": 1, "_summary": "s"}, context=None
    )

    assert result == "value=a"


@pytest.mark.asyncio
async def test_handler_exception_is_reported_as_before():
    registry = ToolRegistry()

    @registry.register(
        name="boom_tool",
        description="boom",
        parameters=_schema(value={"type": "string"}),
    )
    def boom_tool(value: str = "", context=None):
        raise RuntimeError("kaboom")

    result = await registry.execute("boom_tool", {"value": "x"}, context=None)

    assert result == "Error executing boom_tool: kaboom"


def test_coerce_args_keeps_bool_mismatch_for_validation():
    schema = _schema(flag={"type": "integer"})
    assert coerce_args(schema, {"flag": True}) == {"flag": True}
    assert validate_args(schema, {"flag": True})


def test_validate_args_without_schema_reports_nothing():
    assert validate_args(None, {"a": 1}) == []
    assert validate_args({}, {"a": 1}) == []


# ── explicit nulls for optional arguments ────────────────────────────


@pytest.mark.asyncio
async def test_explicit_null_for_optional_argument_is_dropped_before_handler():
    registry = ToolRegistry()
    seen: list[dict] = []

    @registry.register(
        name="grep_like",
        description="grep-like",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "include": {"type": "string"},
            },
            "required": ["pattern"],
        },
    )
    def grep_like(**kwargs):
        seen.append(kwargs)
        return "ok"

    result = await registry.execute("grep_like", {"pattern": "def main", "include": None}, context=None)

    assert result == "ok"
    # The None key is dropped, not forwarded: the handler only sees "pattern".
    assert seen == [{"pattern": "def main"}]
    assert "include" not in coerce_args(
        {"type": "object", "properties": {"include": {"type": "string"}}, "required": []},
        {"include": None},
    )


@pytest.mark.asyncio
async def test_explicit_null_for_required_argument_still_fails_validation():
    registry = ToolRegistry()
    called: list[str] = []

    @registry.register(
        name="required_null_tool",
        description="required",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )
    def required_null_tool(name: str, context=None):
        called.append(name)
        return "ok"

    result = await registry.execute("required_null_tool", {"name": None}, context=None)

    assert result.startswith(INVALID_PREFIX)
    assert "name" in result
    assert called == []


def test_null_is_kept_for_property_that_permits_null():
    schema = _schema(note={"type": ["string", "null"]})
    assert coerce_args(schema, {"note": None}) == {"note": None}

    composed = _schema(note={"anyOf": [{"type": "string"}, {"type": "null"}]})
    assert coerce_args(composed, {"note": None}) == {"note": None}


def test_nested_object_null_property_is_dropped_recursively():
    schema = _schema(
        config={
            "type": "object",
            "properties": {"depth": {"type": "integer"}, "label": {"type": "string"}},
            "required": ["depth"],
        }
    )
    assert coerce_args(schema, {"config": {"depth": 2, "label": None}}) == {"config": {"depth": 2}}

    array_schema = _schema(
        rows={
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "n": {"type": "integer"}}},
        }
    )
    assert coerce_args(array_schema, {"rows": [{"name": None, "n": "2"}]}) == {"rows": [{"n": 2}]}

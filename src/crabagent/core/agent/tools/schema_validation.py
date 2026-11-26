"""Repair and validate tool arguments against their declared JSON Schema.

Models routinely send arguments that are *nearly* right: ``"5"`` where the
schema says ``integer``, a JSON-encoded object where it says ``object``,
``"true"`` where it says ``boolean``. Copilot-style repair (:func:`coerce_args`)
fixes those before :func:`validate_args` reports whatever is still wrong, so a
caller gets one actionable message instead of a handler traceback.

Both helpers are deliberately small and never raise: MCP servers hand us
arbitrary schemas, and a broken schema must not break tool execution.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from jsonschema import Draft202012Validator, validators

logger = logging.getLogger(__name__)

# Fallback for schemas that declare no ``$schema`` (or whose dialect cannot be
# determined): draft 2020-12 accepts the plain draft-07-style objects this repo
# declares and is jsonschema's default in 4.x. Schemas that do declare a
# dialect are validated with that dialect via :func:`_validator_class`.
_DEFAULT_VALIDATOR_CLASS = Draft202012Validator

_INT_RE = re.compile(r"[+-]?\d+")
# A finite JSON number, so "nan"/"inf" are left as text instead of becoming floats.
_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_MAX_DEPTH = 12

# id(schema) -> (schema, validator class). Holding the schema keeps its id
# unique for as long as the entry lives; the cap bounds the memory held.
_VALIDATOR_CACHE: dict[int, tuple[dict[str, Any], type]] = {}
_VALIDATOR_CACHE_MAX = 64


def _validator_class(schema: dict[str, Any]):
    """The validator class for the schema's own dialect, defaulting to 2020-12.

    ``validator_for`` honours a declared ``$schema`` (e.g. draft-04, where
    ``exclusiveMinimum: true`` is legal and means something different from
    2020-12). A schema that fails its own meta-schema falls back to 2020-12 so a
    slightly broken MCP schema still validates as before instead of raising.

    The result is cached by schema identity: ``validate_args`` runs on every
    tool call, and rebuilding (and re-checking) the class each time is pure
    overhead. The value keeps a reference to the schema, so an ``id`` cannot be
    reused by a different object while cached, and the dict is capped at
    :data:`_VALIDATOR_CACHE_MAX` entries with oldest-first eviction so it
    cannot leak.
    """
    key = id(schema)
    cached = _VALIDATOR_CACHE.get(key)
    if cached is not None and cached[0] is schema:
        return cached[1]
    try:
        cls = validators.validator_for(schema, default=_DEFAULT_VALIDATOR_CLASS)
    except Exception:  # noqa: BLE001 - unusable schema, use the default dialect
        cls = _DEFAULT_VALIDATOR_CLASS
    else:
        try:
            cls.check_schema(schema)
        except Exception:  # noqa: BLE001 - declared dialect rejects it; keep validating
            cls = _DEFAULT_VALIDATOR_CLASS
    if len(_VALIDATOR_CACHE) >= _VALIDATOR_CACHE_MAX:
        _VALIDATOR_CACHE.pop(next(iter(_VALIDATOR_CACHE)))
    _VALIDATOR_CACHE[key] = (schema, cls)
    return cls


# --------------------------------------------------------------------------- #
# Small schema helpers
# --------------------------------------------------------------------------- #
def _resolve_pointer(root: Any, pointer: str) -> Any:
    """Resolve a local JSON pointer such as ``/definitions/foo`` against *root*."""
    node = root
    for token in pointer.split("/"):
        if not token:
            continue
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            node = node.get(token)
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            return None
    return node


def _deref(schema: Any, root: Any, _seen: frozenset[str] = frozenset()) -> Any:
    """Follow local ``$ref`` chains; returns the concrete subschema."""
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#") or ref in _seen:
        return schema
    target = _resolve_pointer(root, ref[1:])
    if not isinstance(target, dict):
        return schema
    merged = {key: value for key, value in schema.items() if key != "$ref"}
    merged.update(target)
    return _deref(merged, root, _seen | {ref})


def _types(schema: Any, root: Any) -> set[str]:
    """Every JSON type the schema (or one of its branches) allows."""
    resolved = _deref(schema, root)
    if not isinstance(resolved, dict):
        return set()
    types: set[str] = set()
    declared = resolved.get("type")
    if isinstance(declared, str):
        types.add(declared)
    elif isinstance(declared, list):
        types.update(item for item in declared if isinstance(item, str))
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in resolved.get(key) or ():
            types |= _types(branch, root)
    return types


def _properties(schema: Any, root: Any) -> dict[str, Any]:
    """Property schemas declared directly or inside composition branches."""
    resolved = _deref(schema, root)
    if not isinstance(resolved, dict):
        return {}
    props = dict(resolved.get("properties") or {})
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in resolved.get(key) or ():
            props.update(_properties(branch, root))
    return props


def _items(schema: Any, root: Any) -> Any:
    resolved = _deref(schema, root)
    if not isinstance(resolved, dict):
        return None
    if isinstance(resolved.get("items"), dict):
        return resolved["items"]
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in resolved.get(key) or ():
            sub = _items(branch, root)
            if sub is not None:
                return sub
    return None


def _required(schema: Any, root: Any) -> set[str]:
    """Property names the schema declares as required (union of its branches)."""
    resolved = _deref(schema, root)
    if not isinstance(resolved, dict):
        return set()
    required = {name for name in resolved.get("required") or () if isinstance(name, str)}
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in resolved.get(key) or ():
            required |= _required(branch, root)
    return required


def _permits_null(schema: Any, root: Any) -> bool:
    """Whether the schema itself accepts ``null`` (untyped schemas do)."""
    types = _types(schema, root)
    return not types or "null" in types


def _matches(value: Any, types: set[str]) -> bool:
    """Best-effort check whether *value* already has one of the declared types."""
    if not types:
        return True
    for name in types:
        if name == "object" and isinstance(value, dict):
            return True
        if name == "array" and isinstance(value, list):
            return True
        if name == "string" and isinstance(value, str):
            return True
        if name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if name == "boolean" and isinstance(value, bool):
            return True
        if name == "null" and value is None:
            return True
    return False


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def coerce_args(schema: dict[str, Any] | None, args: dict[str, Any]) -> dict[str, Any]:
    """Repair *args* in place-ish so they satisfy *schema* where that is obvious.

    Only lossless, unambiguous repairs are applied: a JSON string becomes the
    object/array the schema expects, ``"5"``/``"2.5"``/``"true"`` become the
    scalar the schema expects. Everything else is left untouched for
    :func:`validate_args` to report (``bool`` is intentionally not turned into
    ``1``/``0``, and no value is ever guessed).
    """
    if not isinstance(args, dict) or not isinstance(schema, dict) or not schema:
        return args
    try:
        coerced = _coerce(schema, args, schema, 0)
        return coerced if isinstance(coerced, dict) else args
    except Exception:  # pragma: no cover - defensive, a bad schema must not break tools
        logger.debug("Schema coercion failed, passing arguments through", exc_info=True)
        return args


def validate_args(schema: dict[str, Any] | None, args: Any) -> list[str]:
    """Return human-readable problems with *args* against *schema* (empty = valid)."""
    if schema is None or schema == {} or not isinstance(schema, dict):
        return []
    try:
        validator = _validator_class(schema)(schema)
        errors = sorted(
            validator.iter_errors(args),
            key=lambda err: (len(err.absolute_path), list(map(str, err.absolute_path)), err.message),
        )
    except Exception:  # unresolvable $ref / malformed schema - never raise
        logger.debug("Schema validation skipped (unusable schema)", exc_info=True)
        return []
    problems: list[str] = []
    for error in errors:
        path = ".".join(str(part) for part in error.absolute_path) or "(root)"
        problems.append(f"{path}: {error.message}")
    return problems


def coerce_and_validate(schema: dict[str, Any] | None, args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Repair then validate; convenience for callers that want both."""
    coerced = coerce_args(schema, args)
    return coerced, validate_args(schema, coerced)


# --------------------------------------------------------------------------- #
# Coercion internals
# --------------------------------------------------------------------------- #
def _coerce(schema: Any, value: Any, root: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return value
    resolved = _deref(schema, root)
    if not isinstance(resolved, dict):
        return value

    types = _types(resolved, root)

    # Never coerce a value away from a type the schema already allows. In
    # particular a string stays text whenever "string" is one of the allowed
    # types (including inside anyOf/oneOf), so "007" (a code, an ID) is not
    # turned into 7 and "nan" is not silently turned into a float.
    if isinstance(value, str) and "string" in types:
        return value

    # Composition: use the first branch that already fits (or that repairs the
    # value into a fit), then fall through to plain in-place repair.
    for key in ("anyOf", "oneOf"):
        branches = resolved.get(key)
        if isinstance(branches, list) and branches:
            for branch in branches:
                candidate = _coerce(branch, value, root, depth + 1)
                if _matches(candidate, _types(branch, root)):
                    return candidate
    for branch in resolved.get("allOf") or ():
        value = _coerce(branch, value, root, depth + 1)

    # (a) a JSON-encoded container string where the schema wants a container
    if isinstance(value, str) and ("object" in types or "array" in types):
        parsed = _parse_json(value)
        if isinstance(parsed, dict) and "object" in types:
            return _coerce_children(resolved, parsed, root, depth)
        if isinstance(parsed, list) and "array" in types:
            return _coerce_children(resolved, parsed, root, depth)

    # (b) scalar repairs: string -> int / float / bool (never bool -> int)
    if isinstance(value, str):
        text = value.strip()
        if "integer" in types and _INT_RE.fullmatch(text):
            return int(text)
        if "number" in types and _NUMBER_RE.fullmatch(text):
            return float(text)
        if "boolean" in types and text.lower() in ("true", "false"):
            return text.lower() == "true"

    return _coerce_children(resolved, value, root, depth)


def _coerce_children(schema: dict[str, Any], value: Any, root: Any, depth: int) -> Any:
    """Recurse into object properties / array items, repairing their leaves.

    An explicit ``null`` for an optional property whose schema does not accept
    null is dropped: models routinely send ``{"include": null}`` for an unused
    optional argument, and passing no key is what the handler already defaults
    to. Required and null-permitting properties are left for validation.
    """
    if isinstance(value, dict):
        props = _properties(schema, root)
        if not props:
            return value
        required = _required(schema, root)
        repaired: dict[str, Any] = {}
        for key, item in value.items():
            if key not in props:
                repaired[key] = item
                continue
            prop_schema = props[key]
            if item is None and key not in required and not _permits_null(prop_schema, root):
                continue
            repaired[key] = _coerce(prop_schema, item, root, depth + 1)
        return repaired
    if isinstance(value, list):
        item_schema = _items(schema, root)
        if item_schema is None:
            return value
        return [_coerce(item_schema, item, root, depth + 1) for item in value]
    return value

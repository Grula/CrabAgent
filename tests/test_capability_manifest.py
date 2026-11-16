"""Contract tests for the capability manifest data model (atomic task W2).

These tests pin the manifest -> tool-resolution behaviour only: no registry
wiring, no loader side effects. Each test covers one behaviour rule.
"""

from __future__ import annotations

from crabagent.core.agent.capability_manifest import (
    SCHEMA_VERSION,
    CapabilityManifest,
    default_manifest,
    load_manifest,
    resolve_tools,
    select_packs,
)


def test_default_valid():
    """default_manifest() is schema v1, has a non-empty core, no packs, and
    a non-empty session-gated list (never everything-on)."""
    d = default_manifest()
    assert d.schema_version == SCHEMA_VERSION == 1
    assert len(d.core) > 0
    assert d.packs == {}
    assert len(d.requires_session) > 0


def test_load_none_and_empty_fallback():
    """Missing or empty input falls back to default_manifest()."""
    assert load_manifest(None) == default_manifest()
    assert load_manifest({}) == default_manifest()


def test_version_mismatch_fallback():
    """An unknown schema_version is rejected in favour of the safe default."""
    raw = {
        "schema_version": 99,
        "core": ["x"],
        "packs": {},
        "requires_session": [],
    }
    assert load_manifest(raw) == default_manifest()


def test_resolve_tools():
    """core is always present, selected packs add their tools, denied tools and
    (in unattended mode) session-gated tools are removed."""
    m = CapabilityManifest(
        schema_version=1,
        core=("read",),
        packs={"office": ("office_read",)},
        requires_session=("agent_runs_inspect",),
    )

    assert resolve_tools(m, "interactive", None, {"office"}) == {"read", "office_read"}
    assert resolve_tools(m, "interactive", None, set()) == {"read"}
    assert resolve_tools(m, "interactive", {"office_read": "deny"}, {"office"}) == {
        "read"
    }

    m2 = CapabilityManifest(
        schema_version=1,
        core=("read", "agent_runs_inspect"),
        packs={},
        requires_session=("agent_runs_inspect",),
    )
    assert resolve_tools(m2, "unattended", None, set()) == {"read"}


def test_select_packs_override():
    """Task-level packs override profile packs; None means 'not specified'."""
    assert select_packs(["a"], ["b"]) == {"b"}
    assert select_packs(["a"], None) == {"a"}
    assert select_packs(None, None) == set()

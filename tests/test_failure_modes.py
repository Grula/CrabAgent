"""Tests for the known failure-modes preamble (W5).

Contract under test: ``crabagent.core.agent.failure_modes``

* ``known_failure_modes(max_items=5)`` -> list of ``{"anti_pattern", "alternative"}``
  dicts, capped at ``max_items`` and never more than 5 total.
* ``inject_failure_modes(system_prompt, modes)`` -> appends ONE compact block under
  the heading ``Known failure modes to avoid:``; idempotent; no-op for an empty list.

One test per behaviour rule in the spec.
"""

from __future__ import annotations

from crabagent.core.agent.failure_modes import inject_failure_modes, known_failure_modes

HEADING = "Known failure modes to avoid:"


def test_capped():
    """The default list is exactly 5 entries and max_items caps it (never >5)."""
    assert len(known_failure_modes()) == 5
    assert len(known_failure_modes(max_items=3)) == 3
    assert len(known_failure_modes(max_items=99)) == 5


def test_entries_nonempty():
    """Every entry carries a non-empty anti_pattern and alternative."""
    modes = known_failure_modes()
    assert modes

    for entry in modes:
        assert isinstance(entry, dict)
        assert entry["anti_pattern"]
        assert entry["alternative"]


def test_inject_appends_once():
    """The preamble is appended exactly once and re-injection is idempotent."""
    injected = inject_failure_modes("BASE", known_failure_modes())

    assert HEADING in injected
    assert injected.count(HEADING) == 1
    assert "BASE" in injected
    assert inject_failure_modes(injected, known_failure_modes()) == injected


def test_inject_empty():
    """With no failure modes the system prompt is returned unchanged."""
    assert inject_failure_modes("BASE", []) == "BASE"

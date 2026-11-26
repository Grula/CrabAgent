"""How one expense is divided between participants."""

from __future__ import annotations


def split_even(total_cents: int, people: list[str]) -> dict[str, int]:
    """Each participant's share of ``total_cents``."""
    if not people:
        raise ValueError("an expense needs at least one participant")
    if len(set(people)) != len(people):
        raise ValueError("duplicate participant")
    share = total_cents // len(people)
    return {person: share for person in people}

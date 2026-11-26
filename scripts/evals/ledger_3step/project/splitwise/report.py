"""Plain-text views of a ledger."""

from __future__ import annotations

from splitwise.ledger import Ledger
from splitwise.money import format_cents


def render_balances(ledger: Ledger) -> str:
    """One line per person: "alice: +$3.33" (is owed) or "bob: $-3.33" (owes)."""
    lines = []
    for person, cents in ledger.balances().items():
        sign = "+" if cents > 0 else ""
        lines.append(f"{person}: {sign}{format_cents(cents)}")
    return "\n".join(lines)

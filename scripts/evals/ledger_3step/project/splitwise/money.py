"""Amounts are integer cents everywhere; text only at the edges."""

from __future__ import annotations

import re

_AMOUNT = re.compile(r"^\$?(\d+)(?:\.(\d{1,2}))?$")


def parse_amount(text: str) -> int:
    """"12.5" -> 1250, "$3" -> 300. Raises ValueError for anything else."""
    match = _AMOUNT.match((text or "").strip())
    if not match:
        raise ValueError(f"not an amount: {text!r}")
    dollars, cents = match.groups()
    return int(dollars) * 100 + int((cents or "0").ljust(2, "0"))


def format_cents(cents: int) -> str:
    """1250 -> "$12.50"."""
    return f"${cents / 100:.2f}"

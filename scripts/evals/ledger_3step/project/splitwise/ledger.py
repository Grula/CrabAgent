"""Expenses and the balances they produce."""

from __future__ import annotations

from dataclasses import dataclass

from splitwise.split import split_even


@dataclass(frozen=True)
class Expense:
    payer: str
    amount_cents: int
    participants: tuple[str, ...]
    note: str = ""


class Ledger:
    def __init__(self) -> None:
        self.expenses: list[Expense] = []

    def add_expense(self, payer: str, amount_cents: int, participants: list[str], note: str = "") -> None:
        if amount_cents <= 0:
            raise ValueError("amount must be positive")
        self.expenses.append(Expense(payer, amount_cents, tuple(participants), note))

    def balances(self) -> dict[str, int]:
        """Per person: positive = is owed money, negative = owes money. Sorted by name."""
        totals: dict[str, int] = {}
        for expense in self.expenses:
            totals[expense.payer] = totals.get(expense.payer, 0) + expense.amount_cents
            for person, share in split_even(expense.amount_cents, list(expense.participants)).items():
                totals[person] = totals.get(person, 0) - share
        return dict(sorted(totals.items()))

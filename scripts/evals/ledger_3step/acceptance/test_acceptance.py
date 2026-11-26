"""Hidden acceptance tests for the ledger request (request.md)."""

import random

import pytest

from splitwise import Ledger
from splitwise.money import format_cents
from splitwise.report import render_balances, render_settlement
from splitwise.split import split_even


# 1. the missing cent
def test_reported_dinner_sums_to_zero():
    ledger = Ledger()
    ledger.add_expense("alice", 1000, ["alice", "bob", "carol"], "dinner")
    assert ledger.balances() == {"alice": 666, "bob": -333, "carol": -333}


def test_leftover_cents_go_alphabetically_whatever_the_input_order():
    assert split_even(1000, ["carol", "alice", "bob"]) == {"alice": 334, "bob": 333, "carol": 333}
    assert split_even(1001, ["dave", "bob", "carol", "alice"]) == {"alice": 251, "bob": 250, "carol": 250, "dave": 250}
    assert split_even(1002, ["b", "a", "c"]) == {"a": 334, "b": 334, "c": 334}
    assert split_even(2, ["c", "b", "a"]) == {"a": 1, "b": 1, "c": 0}


def test_random_ledgers_always_sum_to_zero():
    rng = random.Random(7)
    names = ["ann", "ben", "cy", "dee", "eli"]
    for _ in range(200):
        ledger = Ledger()
        for _ in range(rng.randint(1, 6)):
            people = rng.sample(names, rng.randint(1, len(names)))
            ledger.add_expense(rng.choice(names), rng.randint(1, 10_000), people)
        assert sum(ledger.balances().values()) == 0


# 2. readable debts
@pytest.mark.parametrize("cents, text", [(-333, "-$3.33"), (-5, "-$0.05"), (0, "$0.00"), (1250, "$12.50")])
def test_format_cents_negative(cents, text):
    assert format_cents(cents) == text


def test_balance_report_signs():
    ledger = Ledger()
    ledger.add_expense("alice", 1000, ["alice", "bob", "carol"])
    ledger.add_expense("dave", 500, ["dave"])
    assert render_balances(ledger) == "alice: +$6.66\nbob: -$3.33\ncarol: -$3.33\ndave: $0.00"


# 3. settlement
def _cleared(ledger, payments):
    left = dict(ledger.balances())
    for payer, receiver, cents in payments:
        assert cents > 0
        left[payer] += cents
        left[receiver] -= cents
    return all(v == 0 for v in left.values())


def test_settle_greedy_example():
    ledger = Ledger()
    ledger.add_expense("alice", 1000, ["alice", "bob", "carol"])
    assert ledger.settle() == [("bob", "alice", 333), ("carol", "alice", 333)]
    assert render_settlement(ledger) == "bob pays alice $3.33\ncarol pays alice $3.33"


def test_settle_largest_first_with_alphabetical_ties():
    ledger = Ledger()
    # balances: ann +900, ben +300, cy -600, dee -600
    ledger.add_expense("ann", 1200, ["ann", "cy", "dee"])
    ledger.add_expense("ben", 600, ["ben", "cy", "dee"])
    ledger.add_expense("ann", 300, ["ann", "ben", "cy", "dee"])
    balances = ledger.balances()
    assert sum(balances.values()) == 0
    payments = ledger.settle()
    assert _cleared(ledger, payments)
    # first payment: the biggest debtor (tie cy/dee -> cy) pays the biggest creditor (ann)
    assert payments[0][0] == "cy" and payments[0][1] == "ann"


def test_settle_exact_sequence():
    ledger = Ledger()
    ledger.add_expense("a", 600, ["a", "b", "c"])   # a +400, b -200, c -200
    ledger.add_expense("d", 300, ["c", "d"])         # d +150, c -150
    # balances: a +400, b -200, c -350, d +150
    assert ledger.balances() == {"a": 400, "b": -200, "c": -350, "d": 150}
    assert ledger.settle() == [("c", "a", 350), ("b", "d", 150), ("b", "a", 50)]


def test_settle_clears_random_ledgers_with_few_payments():
    rng = random.Random(11)
    names = ["ann", "ben", "cy", "dee", "eli"]
    for _ in range(100):
        ledger = Ledger()
        for _ in range(rng.randint(1, 5)):
            ledger.add_expense(rng.choice(names), rng.randint(1, 5000), rng.sample(names, rng.randint(1, 5)))
        payments = ledger.settle()
        assert _cleared(ledger, payments)
        nonzero = sum(1 for v in ledger.balances().values() if v)
        assert len(payments) <= max(0, nonzero - 1)


def test_settle_does_not_change_the_ledger():
    ledger = Ledger()
    ledger.add_expense("alice", 1000, ["alice", "bob"])
    before = ledger.balances()
    ledger.settle()
    assert ledger.balances() == before


def test_nothing_to_settle():
    ledger = Ledger()
    assert ledger.settle() == []
    assert render_settlement(ledger) == "all settled"
    ledger.add_expense("a", 100, ["a"])
    assert render_settlement(ledger) == "all settled"

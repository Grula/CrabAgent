from splitwise import Ledger
from splitwise.report import render_balances


def test_balances_and_report():
    ledger = Ledger()
    ledger.add_expense("alice", 900, ["alice", "bob", "carol"], "pizza")
    assert ledger.balances() == {"alice": 600, "bob": -300, "carol": -300}
    assert render_balances(ledger) == "alice: +$6.00\nbob: $-3.00\ncarol: $-3.00"

Three things for the splitwise project in this workspace. Add tests for each, and keep the test suite passing.

1. **Bug: a cent goes missing.** Three of us split a $10.00 dinner that alice paid. The balances report says alice is owed $6.67 while bob and carol each owe $3.33, so the balances don't add up to zero. Balances must always sum to exactly zero. When an amount doesn't divide evenly, the leftover cents go one each to the participants in alphabetical order (for $10.00 between alice, bob and carol: alice's share is 334, bob's and carol's 333).

2. **Readable debts.** The balances report shows a debt as `bob: $-3.33`. It should read `bob: -$3.33`. Credits stay `alice: +$6.67`, and a settled person shows `dave: $0.00`. `format_cents` itself should format negative amounts as `-$3.33`.

3. **Who pays whom.** Add `Ledger.settle()`, returning the payments that clear every balance as a list of `(payer, receiver, cents)` tuples, and `render_settlement(ledger)` in `report.py`, which renders them one per line as `bob pays alice $3.33` (or `all settled` when there is nothing to pay). Build the list greedily: repeatedly take the person who owes the most and the person who is owed the most (ties broken alphabetically by name), make the payment the smaller of the two amounts, and repeat until everyone is at zero.

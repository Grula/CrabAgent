Add three features to the inventory project in this workspace. Each must come with tests, and the existing tests must keep passing.

1. **Low-stock query.** `Inventory.low_stock(threshold: int) -> list[str]` returns the names of items whose quantity is strictly below `threshold`, sorted alphabetically. A `threshold` below 1 raises `ValueError`.

2. **Persistence.** `Inventory.save(path)` writes the stock to a JSON file shaped `{"items": {"<name>": <qty>, ...}}`. `Inventory.load(path)` is a classmethod that returns an `Inventory` read from such a file; a missing file gives an empty inventory, and a file that isn't valid inventory JSON raises `ValueError` whose message contains the path.

3. **CLI.** The CLI gets a global `--db PATH` option (default `inventory.json`), given before the subcommand. When `main` is called without an `inventory` argument, it loads the inventory from `--db` before the command and saves it back after `add` and `remove` (not after `list` or `low-stock`). Add a `low-stock` subcommand with `--threshold N` (default 5) that prints one item name per line, or `nothing low` when there is none. Calling `main(argv, inventory)` with an inventory keeps working as today and never touches a file.

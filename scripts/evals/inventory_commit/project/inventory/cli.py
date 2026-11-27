"""Command line: python -m inventory.cli add|remove|list ..."""

from __future__ import annotations

import argparse
import sys

from inventory.store import Inventory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inventory")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="add stock")
    add.add_argument("name")
    add.add_argument("qty", type=int, nargs="?", default=1)
    remove = sub.add_parser("remove", help="remove stock")
    remove.add_argument("name")
    remove.add_argument("qty", type=int, nargs="?", default=1)
    sub.add_parser("list", help="list stock")
    return parser


def main(argv: list[str] | None = None, inventory: Inventory | None = None) -> int:
    args = build_parser().parse_args(argv)
    inv = inventory if inventory is not None else Inventory()
    try:
        if args.command == "add":
            inv.add(args.name, args.qty)
        elif args.command == "remove":
            inv.remove(args.name, args.qty)
        elif args.command == "list":
            for name, qty in inv.items():
                print(f"{name}: {qty}")
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

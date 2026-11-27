import pytest

from inventory import Inventory


def test_add_and_remove():
    inv = Inventory()
    inv.add("apple", 3)
    inv.add("apple")
    inv.remove("apple", 2)
    assert inv.quantity("apple") == 2
    inv.remove("apple", 2)
    assert inv.items() == []


def test_rejects_bad_input():
    inv = Inventory()
    with pytest.raises(ValueError):
        inv.add(" ", 1)
    with pytest.raises(ValueError):
        inv.add("x", 0)
    with pytest.raises(KeyError):
        inv.remove("missing")

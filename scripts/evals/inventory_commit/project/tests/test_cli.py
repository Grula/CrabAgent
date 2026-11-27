from inventory import Inventory
from inventory.cli import main


def test_list_prints_sorted_items(capsys):
    inv = Inventory()
    assert main(["add", "pear", "2"], inv) == 0
    assert main(["add", "apple"], inv) == 0
    assert main(["list"], inv) == 0
    assert capsys.readouterr().out == "apple: 1\npear: 2\n"


def test_errors_return_1(capsys):
    assert main(["remove", "ghost"], Inventory()) == 1
    assert "error" in capsys.readouterr().err

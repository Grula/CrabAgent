"""Hidden acceptance tests for the 3-step inventory request (request.md)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from inventory import Inventory
from inventory.cli import main


def _inv(**items):
    inv = Inventory()
    for name, qty in items.items():
        inv.add(name, qty)
    return inv


# 1. low_stock
def test_low_stock_strictly_below_and_sorted():
    inv = _inv(pear=2, apple=5, fig=1, kiwi=9)
    assert inv.low_stock(5) == ["fig", "pear"]
    assert inv.low_stock(10) == ["apple", "fig", "kiwi", "pear"]
    assert inv.low_stock(1) == []


def test_low_stock_rejects_threshold_below_one():
    with pytest.raises(ValueError):
        _inv(a=1).low_stock(0)


# 2. persistence
def test_save_writes_the_documented_shape(tmp_path):
    path = tmp_path / "db.json"
    _inv(apple=3, pear=1).save(path)
    assert json.loads(path.read_text()) == {"items": {"apple": 3, "pear": 1}}


def test_load_round_trip_and_missing_file(tmp_path):
    path = tmp_path / "db.json"
    _inv(apple=3).save(path)
    assert Inventory.load(path).items() == [("apple", 3)]
    assert Inventory.load(tmp_path / "nope.json").items() == []
    assert isinstance(Inventory.load(str(path)), Inventory)  # str paths too


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"items": [1]}', '{"other": {}}'])
def test_load_rejects_bad_files_naming_the_path(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ValueError) as exc:
        Inventory.load(path)
    assert str(path) in str(exc.value)


# 3. CLI
def test_cli_persists_add_and_remove(tmp_path, capsys):
    db = tmp_path / "stock.json"
    assert main(["--db", str(db), "add", "apple", "4"]) == 0
    assert main(["--db", str(db), "add", "pear"]) == 0
    assert main(["--db", str(db), "remove", "apple", "1"]) == 0
    assert json.loads(db.read_text()) == {"items": {"apple": 3, "pear": 1}}
    capsys.readouterr()
    assert main(["--db", str(db), "list"]) == 0
    assert capsys.readouterr().out == "apple: 3\npear: 1\n"


def test_cli_list_and_low_stock_do_not_write(tmp_path):
    db = tmp_path / "stock.json"
    assert main(["--db", str(db), "list"]) == 0
    assert main(["--db", str(db), "low-stock"]) == 0
    assert not db.exists()


def test_cli_low_stock_output(tmp_path, capsys):
    db = tmp_path / "stock.json"
    _inv(apple=7, pear=2, fig=4).save(db)
    assert main(["--db", str(db), "low-stock"]) == 0
    assert capsys.readouterr().out == "fig\npear\n"
    assert main(["--db", str(db), "low-stock", "--threshold", "3"]) == 0
    assert capsys.readouterr().out == "pear\n"
    assert main(["--db", str(db), "low-stock", "--threshold", "1"]) == 0
    assert capsys.readouterr().out == "nothing low\n"


def test_cli_with_an_inventory_never_touches_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    inv = Inventory()
    assert main(["add", "apple", "2"], inv) == 0
    assert main(["low-stock"], inv) == 0
    assert inv.quantity("apple") == 2
    assert capsys.readouterr().out == "apple\n"
    assert list(tmp_path.iterdir()) == []


def test_cli_default_db_is_inventory_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["add", "apple"]) == 0
    assert json.loads((tmp_path / "inventory.json").read_text()) == {"items": {"apple": 1}}


def test_module_entry_point(tmp_path):
    root = Path(__file__).resolve().parent.parent
    run = subprocess.run(
        [sys.executable, "-m", "inventory.cli", "--db", str(tmp_path / "s.json"), "add", "kiwi", "2"],
        cwd=root, capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads((tmp_path / "s.json").read_text()) == {"items": {"kiwi": 2}}

import pytest

from splitwise.split import split_even


def test_even_split():
    assert split_even(900, ["a", "b", "c"]) == {"a": 300, "b": 300, "c": 300}


def test_uneven_split_gives_everyone_the_same_share():
    assert split_even(1000, ["a", "b", "c"]) == {"a": 333, "b": 333, "c": 333}


def test_needs_people():
    with pytest.raises(ValueError):
        split_even(100, [])

import pytest

from splitwise.money import format_cents, parse_amount


@pytest.mark.parametrize("text, cents", [("12.5", 1250), ("$3", 300), ("0.07", 7), ("$10.00", 1000)])
def test_parse_amount(text, cents):
    assert parse_amount(text) == cents


def test_parse_amount_rejects_junk():
    with pytest.raises(ValueError):
        parse_amount("12.345")


def test_format_cents():
    assert format_cents(1250) == "$12.50"
    assert format_cents(7) == "$0.07"

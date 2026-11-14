from decimal import Decimal

import pytest

from app.domain.money import Money, allocate_equal_shares, get_currency


def test_money_uses_currency_minor_unit_precision() -> None:
    assert Money.from_major("12.34", "EUR").minor_units == 1234
    assert Money.from_major("125", "JPY").minor_units == 125
    assert Money.from_major("1.234", "KWD").minor_units == 1234
    assert Money.from_major("0.1", "EUR").major_units == Decimal("0.1")


def test_money_rejects_implicit_rounding_and_floats() -> None:
    with pytest.raises(ValueError, match="at most 2"):
        Money.from_major("1.001", "EUR")
    with pytest.raises(TypeError):
        Money.from_major(1.25, "EUR")  # type: ignore[arg-type]


def test_money_rejects_invalid_currency_and_non_finite_values() -> None:
    with pytest.raises(ValueError, match="Unsupported currency"):
        get_currency("ABC")
    with pytest.raises(ValueError, match="uppercase"):
        get_currency("eur")
    with pytest.raises(ValueError, match="finite"):
        Money.from_major("NaN", "EUR")


def test_money_arithmetic_requires_matching_currencies() -> None:
    assert Money(150, "EUR") + Money(50, "EUR") == Money(200, "EUR")
    assert Money(150, "EUR") - Money(50, "EUR") == Money(100, "EUR")
    with pytest.raises(ValueError, match="different currencies"):
        _ = Money(100, "EUR") + Money(100, "USD")


def test_equal_split_preserves_total_and_assigns_remainder_stably() -> None:
    shares = allocate_equal_shares(Money(1000, "EUR"), ["c", "a", "b"])

    assert shares == {
        "a": Money(334, "EUR"),
        "b": Money(333, "EUR"),
        "c": Money(333, "EUR"),
    }
    assert sum(share.minor_units for share in shares.values()) == 1000


def test_equal_split_is_independent_of_input_order() -> None:
    first = allocate_equal_shares(Money(10, "EUR"), ["z", "a", "m"])
    second = allocate_equal_shares(Money(10, "EUR"), ["m", "z", "a"])
    assert first == second


def test_equal_split_supports_negative_adjustments() -> None:
    shares = allocate_equal_shares(Money(-5, "EUR"), ["b", "a"])
    assert shares == {"a": Money(-3, "EUR"), "b": Money(-2, "EUR")}


@pytest.mark.parametrize("participant_ids", [[], [""], ["a", "a"]])
def test_equal_split_rejects_invalid_participants(participant_ids: list[str]) -> None:
    with pytest.raises(ValueError):
        allocate_equal_shares(Money(100, "EUR"), participant_ids)

"""Domain tests for exact configurable expense allocation."""

import pytest

from app.domain.expense_splits import SplitInput, SplitMode, calculate_split
from app.domain.money import Money


def test_equal_split_is_exact_and_stable() -> None:
    shares = calculate_split(
        Money(100, "EUR"),
        SplitMode.EQUAL,
        [SplitInput("c"), SplitInput("a"), SplitInput("b")],
    )
    assert {share.participant_id: share.amount.minor_units for share in shares} == {
        "a": 34, "b": 33, "c": 33
    }
    assert sum(share.amount.minor_units for share in shares) == 100


def test_fixed_split_requires_exact_total() -> None:
    shares = calculate_split(
        Money(1000, "EUR"),
        SplitMode.FIXED,
        [
            SplitInput("guest", amount_minor_units=250),
            SplitInput("owner", amount_minor_units=750),
        ],
    )
    assert [share.amount.minor_units for share in shares] == [250, 750]
    with pytest.raises(ValueError, match="equal"):
        calculate_split(
            Money(1000, "EUR"),
            SplitMode.FIXED,
            [SplitInput("guest", amount_minor_units=250)],
        )


def test_weighted_split_uses_largest_remainder_and_stable_tie_break() -> None:
    shares = calculate_split(
        Money(100, "EUR"),
        SplitMode.WEIGHTED,
        [SplitInput("b", weight=1), SplitInput("a", weight=1), SplitInput("c", weight=1)],
    )
    assert {share.participant_id: share.amount.minor_units for share in shares} == {
        "a": 34, "b": 33, "c": 33
    }


@pytest.mark.parametrize(
    "inputs",
    [
        [],
        [SplitInput("a"), SplitInput("a")],
        [SplitInput("a", amount_minor_units=0)],
        [SplitInput("a", weight=0)],
        [SplitInput("a", amount_minor_units=100, weight=1)],
    ],
)
def test_invalid_split_inputs_are_rejected(inputs: list[SplitInput]) -> None:
    with pytest.raises(ValueError):
        calculate_split(Money(100, "EUR"), SplitMode.FIXED, inputs)


def test_weighted_split_rejects_missing_or_non_positive_weights() -> None:
    with pytest.raises(ValueError):
        calculate_split(
            Money(100, "EUR"), SplitMode.WEIGHTED, [SplitInput("a", weight=None)]
        )

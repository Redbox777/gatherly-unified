"""Tests for per-currency event balance calculation."""

import pytest

from app.domain.event_balances import (
    BalanceAllocation,
    BalanceExpense,
    BalanceParticipant,
    BalanceSettlement,
    calculate_event_balances,
)


def participants():
    return [
        BalanceParticipant("a", "Alex"),
        BalanceParticipant("b", "Boris"),
        BalanceParticipant("c", "Casey"),
    ]


def test_expenses_and_settlement_produce_exact_net_balances() -> None:
    snapshot = calculate_event_balances(
        participants(),
        [
            BalanceExpense(
                3000, "EUR", "a", "equal",
                (
                    BalanceAllocation("a", 1000),
                    BalanceAllocation("b", 1000),
                    BalanceAllocation("c", 1000),
                ),
            )
        ],
        [BalanceSettlement("b", "a", 500, "EUR")],
    )
    balances = {
        item.participant_id: item.balance_minor_units
        for item in snapshot.balances
    }
    assert balances == {"a": 1500, "b": -500, "c": -1000}
    assert sum(balances.values()) == 0


def test_currencies_are_never_combined() -> None:
    snapshot = calculate_event_balances(
        participants(),
        [
            BalanceExpense(
                1000, "EUR", "a", "equal",
                (BalanceAllocation("b", 1000),),
            ),
            BalanceExpense(
                2000, "USD", "b", "equal",
                (BalanceAllocation("a", 2000),),
            ),
        ],
        [],
    )
    balances = {
        (item.participant_id, item.currency): item.balance_minor_units
        for item in snapshot.balances
    }
    assert balances[("a", "EUR")] == 1000
    assert balances[("b", "EUR")] == -1000
    assert balances[("b", "USD")] == 2000
    assert balances[("a", "USD")] == -2000


def test_unsplit_expenses_are_reported_as_pending_and_excluded() -> None:
    snapshot = calculate_event_balances(
        participants(),
        [BalanceExpense(2500, "EUR", "a", None, ())],
        [],
    )
    assert snapshot.balances == ()
    assert snapshot.pending_expenses[0].count == 1
    assert snapshot.pending_expenses[0].amount_minor_units == 2500


def test_removed_participant_history_is_reported_as_unresolved() -> None:
    snapshot = calculate_event_balances(
        participants(),
        [
            BalanceExpense(
                1000, "EUR", None, "equal",
                (BalanceAllocation(None, 500), BalanceAllocation("a", 500)),
            )
        ],
        [BalanceSettlement(None, "a", 100, "EUR")],
    )
    unresolved = snapshot.unresolved[0]
    assert unresolved.missing_payer_minor_units == 1000
    assert unresolved.removed_participant_allocations_minor_units == 500
    assert unresolved.settlements_with_removed_participant_minor_units == 100
    assert snapshot.balances[0].balance_minor_units == -600


@pytest.mark.parametrize(
    "expense",
    [
        BalanceExpense(0, "EUR", "a", "equal", (BalanceAllocation("a", 0),)),
        BalanceExpense(100, "EUR", "a", "equal", (BalanceAllocation("a", -1),)),
    ],
)
def test_invalid_expense_ledger_rows_are_rejected(expense: BalanceExpense) -> None:
    with pytest.raises(ValueError):
        calculate_event_balances(participants(), [expense], [])


def test_inconsistent_saved_split_is_reported_as_pending() -> None:
    snapshot = calculate_event_balances(
        participants(),
        [BalanceExpense(100, "EUR", "a", "equal", (BalanceAllocation("a", 99),))],
        [],
    )
    assert snapshot.balances == ()
    assert snapshot.pending_expenses[0].amount_minor_units == 100


def test_invalid_settlement_amount_is_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_event_balances(
            participants(), [], [BalanceSettlement("a", "b", 0, "EUR")]
        )

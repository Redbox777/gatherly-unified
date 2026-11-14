"""Exact event balance calculations across expenses and recorded settlements."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from app.domain.money import get_currency


@dataclass(frozen=True, slots=True)
class BalanceParticipant:
    participant_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class BalanceAllocation:
    participant_id: str | None
    amount_minor_units: int


@dataclass(frozen=True, slots=True)
class BalanceExpense:
    amount_minor_units: int
    currency: str
    payer_participant_id: str | None
    split_mode: str | None
    allocations: tuple[BalanceAllocation, ...]


@dataclass(frozen=True, slots=True)
class BalanceSettlement:
    from_participant_id: str | None
    to_participant_id: str | None
    amount_minor_units: int
    currency: str


@dataclass(frozen=True, slots=True)
class ParticipantBalance:
    participant_id: str
    participant_name: str
    currency: str
    balance_minor_units: int


@dataclass(frozen=True, slots=True)
class PendingExpenseTotal:
    currency: str
    count: int
    amount_minor_units: int


@dataclass(frozen=True, slots=True)
class UnresolvedFinanceTotal:
    currency: str
    missing_payer_minor_units: int
    removed_participant_allocations_minor_units: int
    settlements_with_removed_participant_minor_units: int


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    balances: tuple[ParticipantBalance, ...]
    pending_expenses: tuple[PendingExpenseTotal, ...]
    unresolved: tuple[UnresolvedFinanceTotal, ...]


def calculate_event_balances(
    participants: Sequence[BalanceParticipant],
    expenses: Sequence[BalanceExpense],
    settlements: Sequence[BalanceSettlement],
) -> BalanceSnapshot:
    """Calculate net balances without combining currencies or inventing FX rates.

    Positive balances are amounts to receive; negative balances are amounts to pay.
    Expenses without a configured split are excluded and reported as pending.
    Historical records linked to removed participants are retained in unresolved
    totals rather than silently assigned to another participant.
    """
    participant_names = {
        participant.participant_id: participant.display_name
        for participant in participants
    }
    if len(participant_names) != len(participants):
        raise ValueError("Participant IDs must be unique")

    totals: dict[tuple[str, str], int] = {}
    pending: dict[str, list[int]] = {}
    unresolved: dict[str, list[int]] = {}

    def issue_bucket(currency: str) -> list[int]:
        get_currency(currency)
        return unresolved.setdefault(currency, [0, 0, 0])

    for expense in expenses:
        get_currency(expense.currency)
        if (
            isinstance(expense.amount_minor_units, bool)
            or not isinstance(expense.amount_minor_units, int)
            or expense.amount_minor_units <= 0
        ):
            raise ValueError("Expense amount must be positive integer minor units")
        if expense.split_mode is None:
            bucket = pending.setdefault(expense.currency, [0, 0])
            bucket[0] += 1
            bucket[1] += expense.amount_minor_units
            continue
        for allocation in expense.allocations:
            amount = allocation.amount_minor_units
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ValueError("Allocation must be non-negative integer minor units")
        if sum(item.amount_minor_units for item in expense.allocations) != (
            expense.amount_minor_units
        ):
            bucket = pending.setdefault(expense.currency, [0, 0])
            bucket[0] += 1
            bucket[1] += expense.amount_minor_units
            continue

        if (
            expense.payer_participant_id is not None
            and expense.payer_participant_id in participant_names
        ):
            key = (expense.payer_participant_id, expense.currency)
            totals[key] = totals.get(key, 0) + expense.amount_minor_units
        else:
            issue_bucket(expense.currency)[0] += expense.amount_minor_units

        for allocation in expense.allocations:
            amount = allocation.amount_minor_units
            if (
                allocation.participant_id is not None
                and allocation.participant_id in participant_names
            ):
                key = (allocation.participant_id, expense.currency)
                totals[key] = totals.get(key, 0) - amount
            else:
                issue_bucket(expense.currency)[1] += amount

    for settlement in settlements:
        get_currency(settlement.currency)
        amount = settlement.amount_minor_units
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValueError("Settlement amount must be positive integer minor units")
        missing_party = False
        if (
            settlement.from_participant_id is not None
            and settlement.from_participant_id in participant_names
        ):
            key = (settlement.from_participant_id, settlement.currency)
            totals[key] = totals.get(key, 0) + amount
        else:
            missing_party = True
        if (
            settlement.to_participant_id is not None
            and settlement.to_participant_id in participant_names
        ):
            key = (settlement.to_participant_id, settlement.currency)
            totals[key] = totals.get(key, 0) - amount
        else:
            missing_party = True
        if missing_party:
            issue_bucket(settlement.currency)[2] += amount

    currencies = sorted(
        {currency for _, currency in totals}
        | set(pending)
        | set(unresolved)
    )
    balances = tuple(
        ParticipantBalance(
            participant_id=participant.participant_id,
            participant_name=participant.display_name,
            currency=currency,
            balance_minor_units=totals.get((participant.participant_id, currency), 0),
        )
        for currency in currencies
        for participant in sorted(
            participants,
            key=lambda item: (item.display_name.casefold(), item.participant_id),
        )
    )
    return BalanceSnapshot(
        balances=balances,
        pending_expenses=tuple(
            PendingExpenseTotal(currency, values[0], values[1])
            for currency, values in sorted(pending.items())
        ),
        unresolved=tuple(
            UnresolvedFinanceTotal(currency, values[0], values[1], values[2])
            for currency, values in sorted(unresolved.items())
        ),
    )

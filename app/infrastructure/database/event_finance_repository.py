"""Owner-scoped event balances and settlement persistence."""

import sqlite3
from datetime import datetime
from uuid import uuid4

from app.domain.event_balances import (
    BalanceAllocation,
    BalanceExpense,
    BalanceParticipant,
    BalanceSettlement,
    BalanceSnapshot,
    calculate_event_balances,
)
from app.domain.money import get_currency
from app.infrastructure.database.connection import transaction


class EventFinanceRepository:
    """Calculate event balances and record verified participant settlements."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_balances_for_owner(
        self, *, event_id: str, owner_id: str
    ) -> dict[str, object] | None:
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return None
        snapshot = self._calculate_snapshot(event_id)
        return {
            "event_id": event_id,
            "balances": [
                {
                    "participant_id": item.participant_id,
                    "participant_name": item.participant_name,
                    "currency": item.currency,
                    "balance_minor_units": item.balance_minor_units,
                }
                for item in snapshot.balances
            ],
            "pending_expenses": [
                {
                    "currency": item.currency,
                    "count": item.count,
                    "amount_minor_units": item.amount_minor_units,
                }
                for item in snapshot.pending_expenses
            ],
            "unresolved": [
                {
                    "currency": item.currency,
                    "missing_payer_minor_units": item.missing_payer_minor_units,
                    "removed_participant_allocations_minor_units": (
                        item.removed_participant_allocations_minor_units
                    ),
                    "settlements_with_removed_participant_minor_units": (
                        item.settlements_with_removed_participant_minor_units
                    ),
                }
                for item in snapshot.unresolved
            ],
        }

    def list_settlements_for_owner(
        self, *, event_id: str, owner_id: str
    ) -> list[dict[str, object]] | None:
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return None
        rows = self._connection.execute(
            """SELECT id, event_id, from_participant_id, from_participant_name,
                      to_participant_id, to_participant_name, amount_minor_units,
                      currency, settled_at, note, created_at
               FROM event_settlements
               WHERE event_id = ?
               ORDER BY settled_at DESC, created_at DESC, id""",
            (event_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def create_settlement_for_owner(
        self,
        *,
        event_id: str,
        owner_id: str,
        from_participant_id: str,
        to_participant_id: str,
        amount_minor_units: int,
        currency: str,
        settled_at: datetime,
        note: str,
        now: datetime,
    ) -> dict[str, object] | None:
        get_currency(currency)
        if (
            isinstance(amount_minor_units, bool)
            or not isinstance(amount_minor_units, int)
            or amount_minor_units <= 0
        ):
            raise ValueError("Settlement amount must be positive integer minor units")
        normalized_note = note.strip()
        if len(normalized_note) > 1000:
            raise ValueError("Settlement note is too long")
        if from_participant_id == to_participant_id:
            raise ValueError("Settlement participants must be different")

        settlement_id = str(uuid4())
        with transaction(self._connection):
            if not self._event_exists(event_id=event_id, owner_id=owner_id):
                return None
            participants = {
                row["id"]: row["display_name"]
                for row in self._connection.execute(
                    """SELECT id, display_name FROM event_participants
                       WHERE event_id = ?""",
                    (event_id,),
                ).fetchall()
            }
            if from_participant_id not in participants or to_participant_id not in participants:
                raise ValueError("Settlement participants must belong to this event")

            snapshot = self._calculate_snapshot(event_id)
            balances = {
                (item.participant_id, item.currency): item.balance_minor_units
                for item in snapshot.balances
            }
            from_balance = balances.get((from_participant_id, currency), 0)
            to_balance = balances.get((to_participant_id, currency), 0)
            if from_balance >= 0 or to_balance <= 0:
                raise ValueError(
                    "Settlement must go from a participant who owes to one who is owed"
                )
            if amount_minor_units > min(-from_balance, to_balance):
                raise ValueError("Settlement exceeds the outstanding balance")

            self._connection.execute(
                """INSERT INTO event_settlements(
                       id, event_id, from_participant_id, from_participant_name,
                       to_participant_id, to_participant_name, amount_minor_units,
                       currency, settled_at, note, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    settlement_id,
                    event_id,
                    from_participant_id,
                    participants[from_participant_id],
                    to_participant_id,
                    participants[to_participant_id],
                    amount_minor_units,
                    currency,
                    settled_at.isoformat(),
                    normalized_note,
                    now.isoformat(),
                ),
            )
        row = self._connection.execute(
            """SELECT id, event_id, from_participant_id, from_participant_name,
                      to_participant_id, to_participant_name, amount_minor_units,
                      currency, settled_at, note, created_at
               FROM event_settlements WHERE id = ? AND event_id = ?""",
            (settlement_id, event_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def _calculate_snapshot(self, event_id: str) -> BalanceSnapshot:
        participants = [
            BalanceParticipant(row["id"], row["display_name"])
            for row in self._connection.execute(
                """SELECT id, display_name FROM event_participants
                   WHERE event_id = ?""",
                (event_id,),
            ).fetchall()
        ]
        expense_rows = self._connection.execute(
            """SELECT expense.id, expense.amount_minor_units, expense.currency,
                      expense.paid_by_participant_id, split.mode
               FROM event_expenses AS expense
               LEFT JOIN event_expense_splits AS split
                 ON split.expense_id = expense.id
               WHERE expense.event_id = ?
               ORDER BY expense.incurred_at, expense.id""",
            (event_id,),
        ).fetchall()
        allocation_rows = self._connection.execute(
            """SELECT allocation.expense_id, allocation.participant_id,
                      allocation.amount_minor_units
               FROM event_expense_allocations AS allocation
               JOIN event_expenses AS expense ON expense.id = allocation.expense_id
               WHERE expense.event_id = ?
               ORDER BY allocation.expense_id, allocation.id""",
            (event_id,),
        ).fetchall()
        allocations: dict[str, list[BalanceAllocation]] = {}
        for row in allocation_rows:
            allocations.setdefault(row["expense_id"], []).append(
                BalanceAllocation(row["participant_id"], row["amount_minor_units"])
            )
        expenses = [
            BalanceExpense(
                amount_minor_units=row["amount_minor_units"],
                currency=row["currency"],
                payer_participant_id=row["paid_by_participant_id"],
                split_mode=row["mode"],
                allocations=tuple(allocations.get(row["id"], [])),
            )
            for row in expense_rows
        ]
        settlement_rows = self._connection.execute(
            """SELECT from_participant_id, to_participant_id,
                      amount_minor_units, currency
               FROM event_settlements WHERE event_id = ?
               ORDER BY settled_at, id""",
            (event_id,),
        ).fetchall()
        settlements = [
            BalanceSettlement(
                from_participant_id=row["from_participant_id"],
                to_participant_id=row["to_participant_id"],
                amount_minor_units=row["amount_minor_units"],
                currency=row["currency"],
            )
            for row in settlement_rows
        ]
        return calculate_event_balances(participants, expenses, settlements)

    def _event_exists(self, *, event_id: str, owner_id: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM events WHERE id = ? AND owner_id = ?",
            (event_id, owner_id.strip()),
        ).fetchone() is not None

"""Owner-scoped persistence for configurable event expense allocations."""

import sqlite3
from uuid import uuid4

from app.domain.expense_splits import SplitMode, SplitShare
from app.domain.money import Money
from app.infrastructure.database.connection import transaction


class EventExpenseSplitsRepository:
    """Read and replace allocations only for expenses in events owned by the caller."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_for_owner(
        self, *, event_id: str, expense_id: str, owner_id: str
    ) -> dict[str, object] | None:
        expense = self._owned_expense(
            event_id=event_id, expense_id=expense_id, owner_id=owner_id
        )
        if expense is None:
            return None
        split = self._connection.execute(
            "SELECT mode FROM event_expense_splits WHERE expense_id = ?",
            (expense_id,),
        ).fetchone()
        rows = self._connection.execute(
            """SELECT allocation.participant_id, allocation.participant_name,
                      allocation.amount_minor_units, allocation.weight
               FROM event_expense_allocations AS allocation
               WHERE allocation.expense_id = ?
               ORDER BY allocation.participant_name COLLATE NOCASE,
                        allocation.participant_id""",
            (expense_id,),
        ).fetchall()
        return {
            "expense_id": expense_id,
            "mode": split["mode"] if split is not None else None,
            "amount_minor_units": expense["amount_minor_units"],
            "currency": expense["currency"],
            "allocations": [dict(row) for row in rows],
        }

    def replace_for_owner(
        self,
        *,
        event_id: str,
        expense_id: str,
        owner_id: str,
        mode: SplitMode,
        shares: list[SplitShare],
    ) -> dict[str, object] | None:
        expense = self._owned_expense(
            event_id=event_id, expense_id=expense_id, owner_id=owner_id
        )
        if expense is None:
            return None
        if not shares:
            raise ValueError("At least one allocation is required")
        if any(share.amount.currency != expense["currency"] for share in shares):
            raise ValueError("Allocation currency must match the expense currency")
        if sum(share.amount.minor_units for share in shares) != expense["amount_minor_units"]:
            raise ValueError("Allocations must equal the expense total")

        participant_ids = [share.participant_id for share in shares]
        if len(set(participant_ids)) != len(participant_ids):
            raise ValueError("Participant IDs must be unique")
        placeholders = ",".join("?" for _ in participant_ids)
        rows = self._connection.execute(
            f"""SELECT id, display_name FROM event_participants
                WHERE event_id = ? AND id IN ({placeholders})""",
            (event_id, *participant_ids),
        ).fetchall()
        names = {row["id"]: row["display_name"] for row in rows}
        if len(names) != len(participant_ids):
            raise ValueError("Every allocation participant must belong to this event")

        with transaction(self._connection):
            self._connection.execute(
                """INSERT INTO event_expense_splits(expense_id, mode)
                   VALUES (?, ?)
                   ON CONFLICT(expense_id) DO UPDATE SET mode = excluded.mode""",
                (expense_id, mode.value),
            )
            self._connection.execute(
                "DELETE FROM event_expense_allocations WHERE expense_id = ?",
                (expense_id,),
            )
            self._connection.executemany(
                """INSERT INTO event_expense_allocations(
                       id, expense_id, participant_id, participant_name,
                       amount_minor_units, weight
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        str(uuid4()),
                        expense_id,
                        share.participant_id,
                        names[share.participant_id],
                        share.amount.minor_units,
                        share.weight,
                    )
                    for share in shares
                ],
            )
        return self.get_for_owner(
            event_id=event_id, expense_id=expense_id, owner_id=owner_id
        )

    def _owned_expense(
        self, *, event_id: str, expense_id: str, owner_id: str
    ) -> sqlite3.Row | None:
        return self._connection.execute(
            """SELECT expense.amount_minor_units, expense.currency
               FROM event_expenses AS expense
               JOIN events ON events.id = expense.event_id
               WHERE expense.id = ? AND expense.event_id = ?
                 AND events.owner_id = ?""",
            (expense_id, event_id, owner_id.strip()),
        ).fetchone()

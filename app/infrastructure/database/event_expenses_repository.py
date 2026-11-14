"""Owner-scoped persistence for event expenses."""

import sqlite3
from datetime import datetime
from uuid import uuid4

from app.domain.money import get_currency
from app.infrastructure.database.connection import transaction


class EventExpensesRepository:
    """Manage expense records belonging to events owned by the current user."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_for_owner(
        self, *, event_id: str, owner_id: str
    ) -> list[dict[str, object]] | None:
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return None
        rows = self._connection.execute(
            """SELECT expense.id, expense.event_id, expense.title,
                      expense.description, expense.amount_minor_units,
                      expense.currency, expense.paid_by_participant_id,
                      participant.display_name AS paid_by_name,
                      expense.incurred_at, expense.created_at, expense.updated_at
               FROM event_expenses AS expense
               LEFT JOIN event_participants AS participant
                 ON participant.id = expense.paid_by_participant_id
               WHERE expense.event_id = ?
               ORDER BY expense.incurred_at DESC, expense.created_at DESC, expense.id""",
            (event_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def create(
        self,
        *,
        event_id: str,
        owner_id: str,
        title: str,
        description: str,
        amount_minor_units: int,
        currency: str,
        paid_by_participant_id: str,
        incurred_at: datetime,
        now: datetime,
    ) -> dict[str, object] | None:
        values = self._validate(
            title=title,
            description=description,
            amount_minor_units=amount_minor_units,
            currency=currency,
        )
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return None
        if not self._participant_exists(event_id, paid_by_participant_id):
            raise ValueError("Payer must be a participant in this event")

        expense_id = str(uuid4())
        with transaction(self._connection):
            self._connection.execute(
                """INSERT INTO event_expenses(
                    id, event_id, title, description, amount_minor_units,
                    currency, paid_by_participant_id, incurred_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    expense_id,
                    event_id,
                    values[0],
                    values[1],
                    amount_minor_units,
                    currency,
                    paid_by_participant_id,
                    incurred_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return self.get_for_owner(
            expense_id=expense_id, event_id=event_id, owner_id=owner_id
        )

    def update(
        self,
        *,
        expense_id: str,
        event_id: str,
        owner_id: str,
        title: str,
        description: str,
        amount_minor_units: int,
        currency: str,
        paid_by_participant_id: str,
        incurred_at: datetime,
        now: datetime,
    ) -> dict[str, object] | None:
        values = self._validate(
            title=title,
            description=description,
            amount_minor_units=amount_minor_units,
            currency=currency,
        )
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return None
        if not self._participant_exists(event_id, paid_by_participant_id):
            raise ValueError("Payer must be a participant in this event")

        with transaction(self._connection):
            cursor = self._connection.execute(
                """UPDATE event_expenses
                   SET title = ?, description = ?, amount_minor_units = ?,
                       currency = ?, paid_by_participant_id = ?, incurred_at = ?,
                       updated_at = ?
                   WHERE id = ? AND event_id = ?""",
                (
                    values[0],
                    values[1],
                    amount_minor_units,
                    currency,
                    paid_by_participant_id,
                    incurred_at.isoformat(),
                    now.isoformat(),
                    expense_id,
                    event_id,
                ),
            )
        if cursor.rowcount != 1:
            return None
        return self.get_for_owner(
            expense_id=expense_id, event_id=event_id, owner_id=owner_id
        )

    def delete(self, *, expense_id: str, event_id: str, owner_id: str) -> bool:
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return False
        with transaction(self._connection):
            cursor = self._connection.execute(
                "DELETE FROM event_expenses WHERE id = ? AND event_id = ?",
                (expense_id, event_id),
            )
        return cursor.rowcount == 1

    def get_for_owner(
        self, *, expense_id: str, event_id: str, owner_id: str
    ) -> dict[str, object] | None:
        row = self._connection.execute(
            """SELECT expense.id, expense.event_id, expense.title,
                      expense.description, expense.amount_minor_units,
                      expense.currency, expense.paid_by_participant_id,
                      participant.display_name AS paid_by_name,
                      expense.incurred_at, expense.created_at, expense.updated_at
               FROM event_expenses AS expense
               JOIN events ON events.id = expense.event_id
               LEFT JOIN event_participants AS participant
                 ON participant.id = expense.paid_by_participant_id
               WHERE expense.id = ? AND expense.event_id = ?
                 AND events.owner_id = ?""",
            (expense_id, event_id, owner_id.strip()),
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _validate(
        *,
        title: str,
        description: str,
        amount_minor_units: int,
        currency: str,
    ) -> tuple[str, str]:
        normalized_title = title.strip()
        normalized_description = description.strip()
        if not normalized_title or len(normalized_title) > 160:
            raise ValueError("Expense title must contain 1 to 160 characters")
        if len(normalized_description) > 1000:
            raise ValueError("Expense description is too long")
        if isinstance(amount_minor_units, bool) or not isinstance(amount_minor_units, int):
            raise TypeError("amount_minor_units must be an integer")
        if amount_minor_units <= 0:
            raise ValueError("Expense amount must be greater than zero")
        get_currency(currency)
        return normalized_title, normalized_description

    def _event_exists(self, *, event_id: str, owner_id: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM events WHERE id = ? AND owner_id = ?",
            (event_id, owner_id.strip()),
        ).fetchone() is not None

    def _participant_exists(self, event_id: str, participant_id: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM event_participants WHERE id = ? AND event_id = ?",
            (participant_id, event_id),
        ).fetchone() is not None

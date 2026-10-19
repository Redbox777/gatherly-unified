"""SQLite repository for events."""

import sqlite3
from datetime import datetime
from uuid import UUID

from app.domain.events import Event


class EventsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def save(self, event: Event) -> None:
        self._connection.execute(
            "INSERT INTO events(id, owner_id, title, starts_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(event.id),
                event.owner_id,
                event.title,
                event.starts_at.isoformat() if event.starts_at else None,
                event.created_at.isoformat(),
            ),
        )

    def get_for_owner(self, *, event_id: UUID, owner_id: str) -> Event | None:
        row = self._connection.execute(
            "SELECT id, owner_id, title, starts_at, created_at FROM events "
            "WHERE id = ? AND owner_id = ?",
            (str(event_id), owner_id.strip()),
        ).fetchone()
        if row is None:
            return None
        starts_at = datetime.fromisoformat(row["starts_at"]) if row["starts_at"] else None
        return Event(
            id=UUID(row["id"]),
            owner_id=row["owner_id"],
            title=row["title"],
            starts_at=starts_at,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def update_for_owner(
        self, *, event_id: UUID, owner_id: str, title: str, starts_at: datetime | None
    ) -> bool:
        cursor = self._connection.execute(
            "UPDATE events SET title = ?, starts_at = ? WHERE id = ? AND owner_id = ?",
            (
                title,
                starts_at.isoformat() if starts_at else None,
                str(event_id),
                owner_id.strip(),
            ),
        )
        return cursor.rowcount == 1

    def delete_for_owner(self, *, event_id: UUID, owner_id: str) -> bool:
        cursor = self._connection.execute(
            "DELETE FROM events WHERE id = ? AND owner_id = ?",
            (str(event_id), owner_id.strip()),
        )
        return cursor.rowcount == 1

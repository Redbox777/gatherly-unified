"""SQLite persistence for event participants."""

import sqlite3
from datetime import datetime
from uuid import UUID

from app.domain.participants import EventParticipant


class ParticipantsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def add(self, participant: EventParticipant) -> None:
        self._connection.execute(
            "INSERT INTO event_participants(id, event_id, display_name, added_at) "
            "VALUES (?, ?, ?, ?)",
            (
                str(participant.id),
                str(participant.event_id),
                participant.display_name,
                participant.added_at.isoformat(),
            ),
        )

    def list_for_event(self, event_id: UUID) -> list[EventParticipant]:
        rows = self._connection.execute(
            "SELECT id, event_id, display_name, added_at FROM event_participants "
            "WHERE event_id = ? ORDER BY added_at, id",
            (str(event_id),),
        ).fetchall()
        return [
            EventParticipant(
                id=UUID(row["id"]),
                event_id=UUID(row["event_id"]),
                display_name=row["display_name"],
                added_at=datetime.fromisoformat(row["added_at"]),
            )
            for row in rows
        ]

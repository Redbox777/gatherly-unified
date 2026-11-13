"""SQLite persistence and authorization for event notes."""

import sqlite3
from datetime import datetime
from uuid import uuid4

from app.infrastructure.database.connection import transaction


class NotesRepository:
    """Store private and shared event notes with event-membership checks."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_for_event(self, *, event_id: str, user_id: str) -> list[dict[str, object]] | None:
        member = self._is_member(event_id=event_id, user_id=user_id)
        if not member:
            return None
        rows = self._connection.execute(
            """SELECT note.id, note.event_id, note.author_id, note.content,
                      note.visibility, note.created_at, note.updated_at
               FROM event_notes AS note
               WHERE note.event_id = ?
                 AND (note.visibility = 'shared' OR note.author_id = ?)
               ORDER BY note.created_at, note.id""",
            (event_id, user_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def create(
        self, *, event_id: str, author_id: str, content: str,
        visibility: str, now: datetime
    ) -> dict[str, object] | None:
        normalized = content.strip()
        if not normalized or len(normalized) > 5000:
            raise ValueError("Note content must contain 1 to 5000 characters")
        if visibility not in {"private", "shared"}:
            raise ValueError("Unsupported note visibility")
        if not self._is_member(event_id=event_id, user_id=author_id):
            return None
        note_id = str(uuid4())
        timestamp = self._format(now)
        self._connection.execute(
            """INSERT INTO event_notes(
                id, event_id, author_id, content, visibility, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (note_id, event_id, author_id, normalized, visibility, timestamp, timestamp),
        )
        return self.get(note_id=note_id, user_id=author_id)

    def update(
        self, *, event_id: str, note_id: str, user_id: str,
        content: str, visibility: str, now: datetime
    ) -> dict[str, object] | None:
        normalized = content.strip()
        if not normalized or len(normalized) > 5000:
            raise ValueError("Note content must contain 1 to 5000 characters")
        if visibility not in {"private", "shared"}:
            raise ValueError("Unsupported note visibility")
        with transaction(self._connection):
            cursor = self._connection.execute(
                """UPDATE event_notes
                   SET content = ?, visibility = ?, updated_at = ?
                   WHERE id = ? AND event_id = ? AND author_id = ?
                     AND EXISTS (
                       SELECT 1 FROM events
                       WHERE events.id = event_notes.event_id
                         AND (events.owner_id = ? OR EXISTS (
                           SELECT 1 FROM event_participants
                           WHERE event_participants.event_id = events.id
                             AND event_participants.linked_user_id = ?
                         ))
                     )""",
                (
                    normalized, visibility, self._format(now), note_id,
                    event_id, user_id, user_id, user_id,
                ),
            )
        if cursor.rowcount != 1:
            return None
        return self.get(note_id=note_id, user_id=user_id)

    def delete(self, *, event_id: str, note_id: str, user_id: str) -> bool:
        with transaction(self._connection):
            cursor = self._connection.execute(
                """DELETE FROM event_notes
                   WHERE id = ? AND event_id = ?
                     AND (
                       author_id = ?
                       OR (
                         visibility = 'shared'
                         AND EXISTS (
                           SELECT 1 FROM events
                           WHERE events.id = event_notes.event_id
                             AND events.owner_id = ?
                         )
                       )
                     )
                     AND EXISTS (
                       SELECT 1 FROM events
                       WHERE events.id = event_notes.event_id
                         AND (events.owner_id = ? OR EXISTS (
                           SELECT 1 FROM event_participants
                           WHERE event_participants.event_id = events.id
                             AND event_participants.linked_user_id = ?
                         ))
                     )""",
                (note_id, event_id, user_id, user_id, user_id, user_id),
            )
        return cursor.rowcount == 1

    def get(self, *, note_id: str, user_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            """SELECT note.id, note.event_id, note.author_id, note.content,
                      note.visibility, note.created_at, note.updated_at
               FROM event_notes AS note
               JOIN events ON events.id = note.event_id
               WHERE note.id = ?
                 AND (events.owner_id = ? OR EXISTS (
                   SELECT 1 FROM event_participants
                   WHERE event_participants.event_id = events.id
                     AND event_participants.linked_user_id = ?
                 ))
                 AND (note.visibility = 'shared' OR note.author_id = ?)""",
            (note_id, user_id, user_id, user_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def _is_member(self, *, event_id: str, user_id: str) -> bool:
        row = self._connection.execute(
            """SELECT 1 FROM events
               WHERE id = ? AND (
                 owner_id = ? OR EXISTS (
                   SELECT 1 FROM event_participants
                   WHERE event_participants.event_id = events.id
                     AND event_participants.linked_user_id = ?
                 )
               )""",
            (event_id, user_id, user_id),
        ).fetchone()
        return row is not None

    @staticmethod
    def _format(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must be timezone-aware")
        return value.isoformat()

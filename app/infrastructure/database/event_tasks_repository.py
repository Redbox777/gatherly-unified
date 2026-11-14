"""SQLite persistence and owner-scoped authorization for event planning tasks."""

import sqlite3
from datetime import datetime
from uuid import uuid4

from app.infrastructure.database.connection import transaction


class EventTasksRepository:
    """Manage event tasks; only the event owner can read or change them."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_for_owner(self, *, event_id: str, owner_id: str) -> list[dict[str, object]] | None:
        event = self._connection.execute(
            "SELECT 1 FROM events WHERE id = ? AND owner_id = ?",
            (event_id, owner_id.strip()),
        ).fetchone()
        if event is None:
            return None
        rows = self._connection.execute(
            """SELECT task.id, task.event_id, task.title, task.description,
                      task.due_at, task.assignee_participant_id, participant.display_name
               FROM event_tasks AS task
               LEFT JOIN event_participants AS participant
                 ON participant.id = task.assignee_participant_id
               WHERE task.event_id = ?
               ORDER BY task.completed ASC, task.due_at IS NULL, task.due_at, task.created_at""",
            (event_id,),
        ).fetchall()
        return [self._serialize(row) for row in rows]

    def create(
        self, *, event_id: str, owner_id: str, title: str, description: str,
        due_at: str | None, assignee_participant_id: str | None, now: datetime
    ) -> dict[str, object] | None:
        normalized_title = title.strip()
        normalized_description = description.strip()
        if not normalized_title or len(normalized_title) > 160:
            raise ValueError("Task title must contain 1 to 160 characters")
        if len(normalized_description) > 2000:
            raise ValueError("Task description is too long")
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return None
        if not self._valid_assignee(event_id, assignee_participant_id):
            raise ValueError("Assignee must belong to this event")
        task_id = str(uuid4())
        with transaction(self._connection):
            self._connection.execute(
                """INSERT INTO event_tasks(
                    id, event_id, title, description, due_at,
                    assignee_participant_id, completed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (task_id, event_id, normalized_title, normalized_description,
                 due_at, assignee_participant_id, now.isoformat(), now.isoformat()),
            )
        return self.get_for_owner(task_id=task_id, event_id=event_id, owner_id=owner_id)

    def update(
        self, *, task_id: str, event_id: str, owner_id: str, title: str,
        description: str, due_at: str | None, assignee_participant_id: str | None,
        completed: bool
    ) -> dict[str, object] | None:
        normalized_title = title.strip()
        normalized_description = description.strip()
        if not normalized_title or len(normalized_title) > 160:
            raise ValueError("Task title must contain 1 to 160 characters")
        if len(normalized_description) > 2000:
            raise ValueError("Task description is too long")
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return None
        if not self._valid_assignee(event_id, assignee_participant_id):
            raise ValueError("Assignee must belong to this event")
        with transaction(self._connection):
            cursor = self._connection.execute(
                """UPDATE event_tasks
                   SET title = ?, description = ?, due_at = ?,
                       assignee_participant_id = ?, completed = ?,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                   WHERE id = ? AND event_id = ?""",
                (normalized_title, normalized_description, due_at,
                 assignee_participant_id, int(completed), task_id, event_id),
            )
        if cursor.rowcount != 1:
            return None
        return self.get_for_owner(task_id=task_id, event_id=event_id, owner_id=owner_id)

    def set_completed(
        self, *, task_id: str, event_id: str, owner_id: str, completed: bool
    ) -> dict[str, object] | None:
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return None
        with transaction(self._connection):
            cursor = self._connection.execute(
                """UPDATE event_tasks
                   SET completed = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                   WHERE id = ? AND event_id = ?""",
                (int(completed), task_id, event_id),
            )
        if cursor.rowcount != 1:
            return None
        return self.get_for_owner(task_id=task_id, event_id=event_id, owner_id=owner_id)

    def delete(self, *, task_id: str, event_id: str, owner_id: str) -> bool:
        if not self._event_exists(event_id=event_id, owner_id=owner_id):
            return False
        with transaction(self._connection):
            cursor = self._connection.execute(
                "DELETE FROM event_tasks WHERE id = ? AND event_id = ?",
                (task_id, event_id),
            )
        return cursor.rowcount == 1

    def get_for_owner(
        self, *, task_id: str, event_id: str, owner_id: str
    ) -> dict[str, object] | None:
        row = self._connection.execute(
            """SELECT task.id, task.event_id, task.title, task.description,
                      task.due_at, task.assignee_participant_id, participant.display_name,
                      task.completed
               FROM event_tasks AS task
               JOIN events ON events.id = task.event_id
               LEFT JOIN event_participants AS participant
                 ON participant.id = task.assignee_participant_id
               WHERE task.id = ? AND task.event_id = ? AND events.owner_id = ?""",
            (task_id, event_id, owner_id.strip()),
        ).fetchone()
        return self._serialize(row) if row is not None else None

    def _event_exists(self, *, event_id: str, owner_id: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM events WHERE id = ? AND owner_id = ?",
            (event_id, owner_id.strip()),
        ).fetchone() is not None

    def _valid_assignee(self, event_id: str, participant_id: str | None) -> bool:
        if participant_id is None:
            return True
        return self._connection.execute(
            "SELECT 1 FROM event_participants WHERE id = ? AND event_id = ?",
            (participant_id, event_id),
        ).fetchone() is not None

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, object]:
        result = dict(row)
        result["completed"] = bool(result.get("completed", 0))
        return result

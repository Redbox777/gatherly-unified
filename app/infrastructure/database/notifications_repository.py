"""SQLite persistence for the notification inbox, preferences, and reminders."""

from datetime import UTC, datetime, timedelta
import sqlite3
from uuid import uuid4

from app.infrastructure.database.connection import transaction


class NotificationsRepository:
    """Persist inbox entries and idempotently materialize due event reminders."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        user_id: str,
        event_id: str | None,
        kind: str,
        title: str,
        body: str,
        dedupe_key: str,
        now: datetime,
    ) -> None:
        with transaction(self._connection):
            self._connection.execute(
                """INSERT OR IGNORE INTO notifications(
                    id, user_id, event_id, kind, title, body, dedupe_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()), user_id, event_id, kind, title, body,
                    dedupe_key, self._format(now),
                ),
            )

    def list_for_user(self, *, user_id: str, limit: int = 50) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """SELECT id, event_id, kind, title, body, created_at, read_at
               FROM notifications WHERE user_id = ?
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_read(self, *, user_id: str, notification_id: str, now: datetime) -> bool:
        with transaction(self._connection):
            cursor = self._connection.execute(
                """UPDATE notifications SET read_at = ?
                   WHERE id = ? AND user_id = ? AND read_at IS NULL""",
                (self._format(now), notification_id, user_id),
            )
        return cursor.rowcount == 1

    def unread_count(self, *, user_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM notifications WHERE user_id = ? AND read_at IS NULL",
            (user_id,),
        ).fetchone()
        return int(row["total"])

    def get_preferences(self, *, user_id: str) -> dict[str, bool]:
        row = self._connection.execute(
            """SELECT email_enabled, event_updates_enabled,
                      participant_added_enabled, reminders_enabled
               FROM notification_preferences WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        if row is None:
            return {
                "email_enabled": True,
                "event_updates_enabled": True,
                "participant_added_enabled": True,
                "reminders_enabled": True,
            }
        return {
            "email_enabled": bool(row["email_enabled"]),
            "event_updates_enabled": bool(row["event_updates_enabled"]),
            "participant_added_enabled": bool(row["participant_added_enabled"]),
            "reminders_enabled": bool(row["reminders_enabled"]),
        }

    def set_preferences(
        self, *, user_id: str, values: dict[str, bool], now: datetime
    ) -> dict[str, bool]:
        current = self.get_preferences(user_id=user_id)
        current.update(values)
        with transaction(self._connection):
            self._connection.execute(
                """INSERT INTO notification_preferences(
                    user_id, email_enabled, event_updates_enabled,
                    participant_added_enabled, reminders_enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    email_enabled = excluded.email_enabled,
                    event_updates_enabled = excluded.event_updates_enabled,
                    participant_added_enabled = excluded.participant_added_enabled,
                    reminders_enabled = excluded.reminders_enabled,
                    updated_at = excluded.updated_at""",
                (
                    user_id,
                    int(current["email_enabled"]),
                    int(current["event_updates_enabled"]),
                    int(current["participant_added_enabled"]),
                    int(current["reminders_enabled"]),
                    self._format(now),
                ),
            )
        return current

    def set_event_reminders(
        self, *, event_id: str, owner_id: str, offsets: list[int], now: datetime
    ) -> None:
        if any(offset not in {1440, 60, 15} for offset in offsets):
            raise ValueError("Unsupported reminder offset")
        with transaction(self._connection):
            self._connection.execute(
                "DELETE FROM event_reminder_preferences WHERE event_id = ? AND owner_id = ?",
                (event_id, owner_id),
            )
            self._connection.executemany(
                """INSERT INTO event_reminder_preferences(
                    event_id, owner_id, offset_minutes, created_at
                ) VALUES (?, ?, ?, ?)""",
                [
                    (event_id, owner_id, offset, self._format(now))
                    for offset in sorted(set(offsets))
                ],
            )

    def get_event_reminders(self, *, event_id: str, owner_id: str) -> list[int]:
        rows = self._connection.execute(
            """SELECT offset_minutes FROM event_reminder_preferences
               WHERE event_id = ? AND owner_id = ? ORDER BY offset_minutes DESC""",
            (event_id, owner_id),
        ).fetchall()
        return [int(row["offset_minutes"]) for row in rows]

    def materialize_due_reminders(self, *, now: datetime) -> int:
        """Create one inbox item per recipient/reminder; unique keys prevent duplicates."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        now_utc = now.astimezone(UTC)
        rows = self._connection.execute(
            """SELECT reminder.event_id, reminder.owner_id, reminder.offset_minutes,
                      event.title, event.starts_at
               FROM event_reminder_preferences AS reminder
               JOIN events AS event ON event.id = reminder.event_id
               WHERE event.starts_at IS NOT NULL"""
        ).fetchall()
        created = 0
        with transaction(self._connection):
            for row in rows:
                starts_at = datetime.fromisoformat(str(row["starts_at"]))
                if starts_at.tzinfo is None or starts_at.utcoffset() is None:
                    continue
                due_at = starts_at.astimezone(UTC) - timedelta(
                    minutes=int(row["offset_minutes"])
                )
                if due_at > now_utc or starts_at.astimezone(UTC) <= now_utc:
                    continue
                event_id = str(row["event_id"])
                owner_id = str(row["owner_id"])
                recipients = {owner_id}
                linked_rows = self._connection.execute(
                    """SELECT linked_user_id FROM event_participants
                       WHERE event_id = ? AND linked_user_id IS NOT NULL""",
                    (event_id,),
                ).fetchall()
                recipients.update(str(item["linked_user_id"]) for item in linked_rows)
                for user_id in recipients:
                    preference = self.get_preferences(user_id=user_id)
                    if not preference["reminders_enabled"]:
                        continue
                    cursor = self._connection.execute(
                        """INSERT OR IGNORE INTO notifications(
                            id, user_id, event_id, kind, title, body, dedupe_key, created_at
                        ) VALUES (?, ?, ?, 'event_reminder', ?, ?, ?, ?)""",
                        (
                            str(uuid4()), user_id, event_id,
                            "Скоро встреча",
                            f"«{row['title']}» начнётся через {row['offset_minutes']} мин.",
                            f"reminder:{event_id}:{row['offset_minutes']}:{user_id}",
                            self._format(now_utc),
                        ),
                    )
                    created += cursor.rowcount
        return created

    def pending_email(self, *, limit: int = 100) -> list[dict[str, str]]:
        rows = self._connection.execute(
            """SELECT notification.id, notification.title, notification.body,
                      identity.provider_subject AS email
               FROM notifications AS notification
               JOIN auth_identities AS identity
                 ON identity.user_id = notification.user_id
                AND identity.provider = 'email' AND identity.verified_at IS NOT NULL
               LEFT JOIN notification_preferences AS preference
                 ON preference.user_id = notification.user_id
               WHERE notification.email_sent_at IS NULL
                 AND notification.email_attempts < 5
                 AND COALESCE(preference.email_enabled, 1) = 1
               ORDER BY notification.created_at, notification.id LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_email_failure(
        self, *, notification_id: str, error_name: str
    ) -> None:
        with transaction(self._connection):
            self._connection.execute(
                """UPDATE notifications
                   SET email_attempts = email_attempts + 1, email_last_error = ?
                   WHERE id = ? AND email_sent_at IS NULL""",
                (error_name[:120], notification_id),
            )

    def record_email_sent(self, *, notification_id: str, now: datetime) -> None:
        with transaction(self._connection):
            self._connection.execute(
                """UPDATE notifications
                   SET email_attempts = email_attempts + 1,
                       email_sent_at = ?, email_last_error = NULL
                   WHERE id = ? AND email_sent_at IS NULL""",
                (self._format(now), notification_id),
            )

    @staticmethod
    def _format(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )

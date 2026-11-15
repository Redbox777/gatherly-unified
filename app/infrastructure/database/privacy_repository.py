"""Privacy operations for user-access export and account erasure."""

import sqlite3
from typing import Any

from app.infrastructure.database.connection import transaction


class PrivacyRepository:
    """Export a user's account data and erase their account transactionally."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def export_user_data(self, *, user_id: str) -> dict[str, Any] | None:
        """Return portable account data while excluding credential and bearer secrets."""
        user = self._connection.execute(
            "SELECT id, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            return None

        identities = self._rows(
            """SELECT provider, provider_subject, verified_at, created_at
               FROM auth_identities WHERE user_id = ? ORDER BY provider""",
            (user_id,),
        )
        sessions = self._rows(
            """SELECT created_at, expires_at, revoked_at
               FROM auth_sessions WHERE user_id = ? ORDER BY created_at""",
            (user_id,),
        )
        events = self._rows(
            """SELECT id, owner_id, title, starts_at, created_at
               FROM events WHERE owner_id = ? ORDER BY created_at, id""",
            (user_id,),
        )
        event_ids = [str(row["id"]) for row in events]
        participants = self._rows(
            """SELECT id, event_id, display_name, added_at, linked_user_id
               FROM event_participants
               WHERE linked_user_id = ? OR event_id IN (
                   SELECT id FROM events WHERE owner_id = ?
               )
               ORDER BY event_id, added_at, id""",
            (user_id, user_id),
        )
        tasks = self._rows_for_events(
            "event_tasks",
            "event_id",
            event_ids,
            "id, event_id, title, description, due_at, assignee_participant_id, "
            "completed, created_at, updated_at",
        )
        venues = self._rows_for_events(
            "event_venues",
            "event_id",
            event_ids,
            "event_id, venue_name, address, latitude, longitude",
        )
        expenses = self._rows_for_events(
            "event_expenses",
            "event_id",
            event_ids,
            "id, event_id, title, description, amount_minor_units, currency, "
            "paid_by_participant_id, paid_by_participant_name, incurred_at, "
            "created_at, updated_at",
        )
        expense_ids = [str(row["id"]) for row in expenses]
        expense_splits = self._rows_for_ids(
            "event_expense_splits",
            "expense_id",
            expense_ids,
            "expense_id, mode",
        )
        expense_allocations = self._rows_for_ids(
            "event_expense_allocations",
            "expense_id",
            expense_ids,
            "id, expense_id, participant_id, participant_name, amount_minor_units, weight",
        )
        settlements = self._rows_for_events(
            "event_settlements",
            "event_id",
            event_ids,
            "id, event_id, from_participant_id, from_participant_name, "
            "to_participant_id, to_participant_name, amount_minor_units, currency, "
            "settled_at, note, created_at",
        )
        notes = self._rows(
            """SELECT id, event_id, author_id, content, visibility, created_at, updated_at
               FROM event_notes
               WHERE author_id = ? OR (
                   visibility = 'shared' AND event_id IN (
                       SELECT id FROM events WHERE owner_id = ?
                   )
               )
               ORDER BY created_at, id""",
            (user_id, user_id),
        )
        invitations = self._rows(
            """SELECT id, participant_id, target_email, created_by_owner_id,
                      created_at, expires_at, accepted_at, revoked_at
               FROM participant_invitations
               WHERE created_by_owner_id = ?
                  OR lower(target_email) IN (
                      SELECT lower(provider_subject) FROM auth_identities
                      WHERE user_id = ? AND provider = 'email'
                  )
                  OR participant_id IN (
                      SELECT participant.id FROM event_participants AS participant
                      JOIN events ON events.id = participant.event_id
                      WHERE events.owner_id = ?
                  )
               ORDER BY created_at, id""",
            (user_id, user_id, user_id),
        )
        notifications = self._rows(
            """SELECT id, event_id, kind, title, body, created_at, read_at,
                      email_sent_at, email_attempts
               FROM notifications WHERE user_id = ? ORDER BY created_at, id""",
            (user_id,),
        )
        notification_preferences = self._rows(
            """SELECT email_enabled, event_updates_enabled,
                      participant_added_enabled, reminders_enabled, updated_at
               FROM notification_preferences WHERE user_id = ?""",
            (user_id,),
        )
        reminder_preferences = self._rows(
            """SELECT event_id, offset_minutes, created_at
               FROM event_reminder_preferences WHERE owner_id = ?
               ORDER BY event_id, offset_minutes""",
            (user_id,),
        )

        return {
            "export_format": "gatherly-account-export-v1",
            "account": dict(user),
            "identities": identities,
            "sessions": sessions,
            "events": events,
            "participants": participants,
            "tasks": tasks,
            "venues": venues,
            "expenses": expenses,
            "expense_splits": expense_splits,
            "expense_allocations": expense_allocations,
            "settlements": settlements,
            "notes": notes,
            "participant_invitations": invitations,
            "notifications": notifications,
            "notification_preferences": notification_preferences,
            "event_reminder_preferences": reminder_preferences,
            "excluded_secrets": [
                "password hashes",
                "session token hashes",
                "email verification and password-reset token hashes",
                "participant invitation token hashes",
            ],
        }

    def delete_user_account(self, *, user_id: str) -> bool:
        """Erase an account and owned data while detaching shared participant records."""
        if not user_id.strip():
            raise ValueError("User ID must not be blank")
        with transaction(self._connection):
            user = self._connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None:
                return False
            email_addresses = [
                str(row["provider_subject"])
                for row in self._connection.execute(
                    """SELECT provider_subject FROM auth_identities
                       WHERE user_id = ? AND provider = 'email'""",
                    (user_id,),
                ).fetchall()
            ]
            self._connection.execute(
                "DELETE FROM participant_invitations WHERE created_by_owner_id = ?",
                (user_id,),
            )
            for email in email_addresses:
                self._connection.execute(
                    "DELETE FROM participant_invitations WHERE lower(target_email) = lower(?)",
                    (email,),
                )
            self._connection.execute(
                "DELETE FROM event_reminder_preferences WHERE owner_id = ?",
                (user_id,),
            )
            owned_event_ids = {
                str(row["id"])
                for row in self._connection.execute(
                    "SELECT id FROM events WHERE owner_id = ?", (user_id,)
                ).fetchall()
            }
            linked_participants = self._connection.execute(
                """SELECT id FROM event_participants
                   WHERE linked_user_id = ? AND event_id NOT IN (
                       SELECT id FROM events WHERE owner_id = ?
                   )""",
                (user_id, user_id),
            ).fetchall()
            for participant in linked_participants:
                participant_id = str(participant["id"])
                anonymized_name = f"Former member {participant_id[:8]}"
                self._connection.execute(
                    """UPDATE event_participants
                       SET display_name = ?, linked_user_id = NULL WHERE id = ?""",
                    (anonymized_name, participant_id),
                )
                self._connection.execute(
                    """UPDATE event_expense_allocations
                       SET participant_name = ? WHERE participant_id = ?""",
                    (anonymized_name, participant_id),
                )
                self._connection.execute(
                    """UPDATE event_expenses
                       SET paid_by_participant_name = ?
                       WHERE paid_by_participant_id = ?""",
                    (anonymized_name, participant_id),
                )
                self._connection.execute(
                    """UPDATE event_settlements
                       SET from_participant_name = ?
                       WHERE from_participant_id = ?""",
                    (anonymized_name, participant_id),
                )
                self._connection.execute(
                    """UPDATE event_settlements
                       SET to_participant_name = ?
                       WHERE to_participant_id = ?""",
                    (anonymized_name, participant_id),
                )
            self._connection.execute("DELETE FROM events WHERE owner_id = ?", (user_id,))
            # Foreign keys cascade identities, sessions, tokens, notifications,
            # preferences, and authored notes. Shared event finance records are
            # retained under a non-identifying participant label.
            cursor = self._connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount == 1

    def _rows(self, query: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connection.execute(query, parameters).fetchall()]

    def _rows_for_events(
        self,
        table: str,
        column: str,
        event_ids: list[str],
        fields: str,
    ) -> list[dict[str, Any]]:
        return self._rows_for_ids(table, column, event_ids, fields)

    def _rows_for_ids(
        self,
        table: str,
        column: str,
        identifiers: list[str],
        fields: str,
    ) -> list[dict[str, Any]]:
        if not identifiers:
            return []
        # These identifiers are internal constants at call sites, never request input.
        placeholders = ",".join("?" for _ in identifiers)
        query = f"SELECT {fields} FROM {table} WHERE {column} IN ({placeholders})"
        return self._rows(query, tuple(identifiers))

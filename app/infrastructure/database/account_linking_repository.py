"""Transactional merging of two independently authenticated Gatherly accounts."""

from datetime import UTC, datetime
import sqlite3

from app.infrastructure.database.connection import transaction


class AccountLinkConflict(ValueError):
    """The two accounts cannot be merged without an identity conflict."""


class AccountLinkingRepository:
    """Merge a source account into the currently authenticated destination account."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def merge_accounts(
        self,
        *,
        target_user_id: str,
        source_user_id: str,
        proof_identity_id: str,
        proof_provider: str,
        expected_password_hash: str | None,
        now: datetime,
    ) -> None:
        """Move identities and user-owned data atomically, then erase the source user."""
        self._require_aware(now)
        if not target_user_id.strip() or not source_user_id.strip():
            raise ValueError("User IDs must not be blank")
        if target_user_id == source_user_id:
            raise AccountLinkConflict("The identity is already linked to this account")
        if proof_provider not in {"email", "telegram"}:
            raise ValueError("Unsupported identity provider")

        timestamp = self._format(now)
        with transaction(self._connection):
            target = self._connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (target_user_id,)
            ).fetchone()
            source = self._connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (source_user_id,)
            ).fetchone()
            if target is None or source is None:
                raise AccountLinkConflict("Account no longer exists")

            proof = self._connection.execute(
                """SELECT user_id, provider, password_hash, verified_at
                   FROM auth_identities WHERE id = ?""",
                (proof_identity_id,),
            ).fetchone()
            if (
                proof is None
                or proof["user_id"] != source_user_id
                or proof["provider"] != proof_provider
                or proof["verified_at"] is None
                or (
                    proof_provider == "email"
                    and (
                        expected_password_hash is None
                        or proof["password_hash"] != expected_password_hash
                    )
                )
            ):
                raise AccountLinkConflict("The second identity could not be re-verified")

            source_providers = {
                str(row["provider"])
                for row in self._connection.execute(
                    "SELECT DISTINCT provider FROM auth_identities WHERE user_id = ?",
                    (source_user_id,),
                ).fetchall()
            }
            target_providers = {
                str(row["provider"])
                for row in self._connection.execute(
                    "SELECT DISTINCT provider FROM auth_identities WHERE user_id = ?",
                    (target_user_id,),
                ).fetchall()
            }
            if not source_providers or proof_provider not in source_providers:
                raise AccountLinkConflict("No verified identity is available to link")
            if source_providers & target_providers:
                raise AccountLinkConflict(
                    "Both accounts already have an identity from the same provider"
                )

            # Move provider identities first; email tokens remain attached to their
            # identity IDs and therefore follow the identity without exposing tokens.
            self._connection.execute(
                "UPDATE auth_identities SET user_id = ? WHERE user_id = ?",
                (target_user_id, source_user_id),
            )

            # Move all account-owned records. Shared event participant records remain
            # intact; only their optional account association changes.
            for table, column in (
                ("events", "owner_id"),
                ("event_notes", "author_id"),
                ("notifications", "user_id"),
                ("event_participants", "linked_user_id"),
                ("participant_invitations", "created_by_owner_id"),
                ("event_reminder_preferences", "owner_id"),
            ):
                self._connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                    (target_user_id, source_user_id),
                )

            # Keep destination notification preferences when present; otherwise
            # retain the source account's preferences.
            self._connection.execute(
                """INSERT OR IGNORE INTO notification_preferences(
                       user_id, email_enabled, event_updates_enabled,
                       participant_added_enabled, reminders_enabled,
                       telegram_enabled, telegram_consent_at,
                       telegram_consent_version, updated_at
                   )
                   SELECT ?, email_enabled, event_updates_enabled,
                          participant_added_enabled, reminders_enabled,
                          telegram_enabled, telegram_consent_at,
                          telegram_consent_version, ?
                   FROM notification_preferences WHERE user_id = ?""",
                (target_user_id, timestamp, source_user_id),
            )
            self._connection.execute(
                "DELETE FROM notification_preferences WHERE user_id = ?",
                (source_user_id,),
            )

            # Invalidate every old browser session. The HTTP adapter issues one new
            # session for the destination after this transaction commits.
            self._connection.execute(
                """UPDATE auth_sessions SET revoked_at = ?
                   WHERE user_id IN (?, ?) AND revoked_at IS NULL""",
                (timestamp, target_user_id, source_user_id),
            )

            self._connection.execute("DELETE FROM users WHERE id = ?", (source_user_id,))

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")

    @classmethod
    def _format(cls, value: datetime) -> str:
        cls._require_aware(value)
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )

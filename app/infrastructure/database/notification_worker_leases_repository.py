"""SQLite-backed lease for ensuring only one notification worker runs at a time."""

from datetime import UTC, datetime, timedelta
import sqlite3

from app.infrastructure.database.connection import transaction


class NotificationWorkerLeaseRepository:
    """Acquire an expiring, cross-process lease for the one-shot notification worker."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def acquire(
        self,
        *,
        worker_name: str,
        owner_token: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool:
        self._validate(worker_name=worker_name, owner_token=owner_token, now=now,
                       lease_duration=lease_duration)
        timestamp = self._format(now)
        expires_at = self._format(now + lease_duration)
        with transaction(self._connection):
            cursor = self._connection.execute(
                """INSERT INTO notification_worker_leases(
                       worker_name, owner_token, lease_expires_at, updated_at
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(worker_name) DO UPDATE SET
                       owner_token = excluded.owner_token,
                       lease_expires_at = excluded.lease_expires_at,
                       updated_at = excluded.updated_at
                   WHERE notification_worker_leases.lease_expires_at <= excluded.updated_at""",
                (worker_name, owner_token, expires_at, timestamp),
            )
        return cursor.rowcount == 1

    def renew(
        self,
        *,
        worker_name: str,
        owner_token: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool:
        self._validate(worker_name=worker_name, owner_token=owner_token, now=now,
                       lease_duration=lease_duration)
        timestamp = self._format(now)
        with transaction(self._connection):
            cursor = self._connection.execute(
                """UPDATE notification_worker_leases
                   SET lease_expires_at = ?, updated_at = ?
                   WHERE worker_name = ? AND owner_token = ?
                     AND lease_expires_at > ?""",
                (
                    self._format(now + lease_duration),
                    timestamp,
                    worker_name,
                    owner_token,
                    timestamp,
                ),
            )
        return cursor.rowcount == 1

    def release(self, *, worker_name: str, owner_token: str) -> bool:
        if not worker_name.strip() or not owner_token.strip():
            raise ValueError("Worker name and owner token are required")
        with transaction(self._connection):
            cursor = self._connection.execute(
                """DELETE FROM notification_worker_leases
                   WHERE worker_name = ? AND owner_token = ?""",
                (worker_name, owner_token),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _validate(
        *,
        worker_name: str,
        owner_token: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> None:
        if not worker_name.strip() or not owner_token.strip():
            raise ValueError("Worker name and owner token are required")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Worker lease timestamps must be timezone-aware")
        if lease_duration.total_seconds() <= 0:
            raise ValueError("Worker lease duration must be positive")

    @staticmethod
    def _format(value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

"""Shared SQLite-backed quotas for outbound email delivery."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import math
import sqlite3
from uuid import uuid4

from app.infrastructure.database.connection import transaction


class SmtpRateLimitsRepository:
    """Atomically limit SMTP attempts across app processes sharing one database."""

    RECIPIENT_LIMIT = 3
    RECIPIENT_WINDOW = timedelta(minutes=15)
    REQUESTER_LIMIT = 5
    REQUESTER_WINDOW = timedelta(minutes=1)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @staticmethod
    def _key_hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    def consume(self, *, recipient: str, requester: str, now: datetime) -> int | None:
        """Reserve one email attempt; return Retry-After seconds when blocked.

        Recipient quotas are shared across all mail purposes and source IPs.
        Requester quotas are shared across all outbound-email endpoints.
        No raw email address or IP address is persisted.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        normalized_recipient = recipient.strip().casefold()
        normalized_requester = requester.strip() or "unknown"
        if not normalized_recipient:
            raise ValueError("recipient must not be empty")

        now_utc = now.astimezone(UTC)
        policies = (
            (
                self._key_hash(f"recipient:{normalized_recipient}"),
                self.RECIPIENT_LIMIT,
                self.RECIPIENT_WINDOW,
            ),
            (
                self._key_hash(f"requester:{normalized_requester}"),
                self.REQUESTER_LIMIT,
                self.REQUESTER_WINDOW,
            ),
        )
        now_text = now_utc.isoformat()
        retry_after: int | None = None

        with transaction(self._connection):
            self._connection.execute(
                "DELETE FROM smtp_rate_limit_events WHERE created_at < ?",
                ((now_utc - timedelta(days=1)).isoformat(),),
            )
            for key_hash, limit, window in policies:
                cutoff = (now_utc - window).isoformat()
                rows = self._connection.execute(
                    """
                    SELECT created_at
                    FROM smtp_rate_limit_events
                    WHERE key_hash = ? AND created_at > ?
                    ORDER BY created_at ASC
                    """,
                    (key_hash, cutoff),
                ).fetchall()
                if len(rows) >= limit:
                    oldest = datetime.fromisoformat(rows[0]["created_at"])
                    wait = (oldest + window - now_utc).total_seconds()
                    seconds = max(1, math.ceil(wait))
                    retry_after = max(retry_after or 0, seconds)

            if retry_after is None:
                self._connection.executemany(
                    """
                    INSERT INTO smtp_rate_limit_events(id, key_hash, created_at)
                    VALUES (?, ?, ?)
                    """,
                    [(str(uuid4()), key_hash, now_text) for key_hash, _, _ in policies],
                )

        return retry_after

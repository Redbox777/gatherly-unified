"""Shared SQLite-backed throttling for sensitive HTTP routes."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import math
import sqlite3
from uuid import uuid4

from app.infrastructure.database.connection import transaction


class HttpRateLimitsRepository:
    """Atomically limit a requester on a route across processes sharing SQLite."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @staticmethod
    def _key_hash(*, requester: str, route: str) -> str:
        material = f"http:{requester.strip() or 'unknown'}:{route}"
        return sha256(material.encode("utf-8")).hexdigest()

    def consume(
        self,
        *,
        requester: str,
        route: str,
        now: datetime,
        limit: int = 5,
        window_seconds: int = 60,
    ) -> int | None:
        """Reserve a request slot, or return seconds until the oldest slot expires."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if not requester.strip() or not route.startswith("/") or route == "/":
            raise ValueError("requester and route must identify a request scope")
        if limit < 1 or window_seconds < 1:
            raise ValueError("limit and window_seconds must be positive")

        now_utc = now.astimezone(UTC)
        now_text = now_utc.isoformat()
        key_hash = self._key_hash(requester=requester, route=route)
        cutoff = (now_utc - timedelta(seconds=window_seconds)).isoformat()
        retry_after: int | None = None

        with transaction(self._connection):
            # Retain only one day of history; this bounds storage independently
            # of the configured one-minute request window.
            self._connection.execute(
                "DELETE FROM http_rate_limit_events WHERE created_at < ?",
                ((now_utc - timedelta(days=1)).isoformat(),),
            )
            rows = self._connection.execute(
                """
                SELECT created_at
                FROM http_rate_limit_events
                WHERE key_hash = ? AND created_at > ?
                ORDER BY created_at ASC
                """,
                (key_hash, cutoff),
            ).fetchall()
            if len(rows) >= limit:
                oldest = datetime.fromisoformat(rows[0]["created_at"])
                wait = (oldest + timedelta(seconds=window_seconds) - now_utc).total_seconds()
                retry_after = max(1, math.ceil(wait))
            else:
                self._connection.execute(
                    """
                    INSERT INTO http_rate_limit_events(id, key_hash, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (str(uuid4()), key_hash, now_text),
                )

        return retry_after

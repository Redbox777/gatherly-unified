"""Persistence operations for server-managed authentication sessions."""

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from uuid import uuid4

from app.domain.auth_tokens import hash_token, issue_token


@dataclass(frozen=True, slots=True)
class CreatedSession:
    """A newly created session; the raw token is returned only once."""

    session_id: str
    user_id: str
    raw_token: str
    created_at: datetime
    expires_at: datetime


class AuthSessionsRepository:
    """Store token digests and validate sessions against expiry and revocation."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection so application services can own transactions."""
        return self._connection

    def create(self, *, user_id: str, now: datetime, expires_at: datetime) -> CreatedSession:
        """Create a session, persisting only the digest of its random token."""
        self._validate_times(now=now, expires_at=expires_at)
        issued = issue_token()
        session_id = str(uuid4())
        self._connection.execute(
            """INSERT INTO auth_sessions(
                id, user_id, token_hash, created_at, expires_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, NULL)""",
            (
                session_id,
                user_id,
                issued.digest,
                self._format(now),
                self._format(expires_at),
            ),
        )
        return CreatedSession(
            session_id=session_id,
            user_id=user_id,
            raw_token=issued.raw,
            created_at=now,
            expires_at=expires_at,
        )

    def get_active_user_id(self, *, raw_token: str, now: datetime) -> str | None:
        """Return the session owner only when the token is valid and active."""
        self._require_aware(now)
        row = self._connection.execute(
            """SELECT user_id FROM auth_sessions
            WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?""",
            (hash_token(raw_token), self._format(now)),
        ).fetchone()
        return str(row["user_id"]) if row is not None else None

    def revoke(self, *, raw_token: str, now: datetime) -> bool:
        """Revoke a session once; return False for unknown or already revoked tokens."""
        self._require_aware(now)
        cursor = self._connection.execute(
            """UPDATE auth_sessions SET revoked_at = ?
            WHERE token_hash = ? AND revoked_at IS NULL""",
            (self._format(now), hash_token(raw_token)),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")

    @classmethod
    def _validate_times(cls, *, now: datetime, expires_at: datetime) -> None:
        cls._require_aware(now)
        cls._require_aware(expires_at)
        if expires_at <= now:
            raise ValueError("Session expiry must be after creation")

    @classmethod
    def _format(cls, value: datetime) -> str:
        cls._require_aware(value)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )

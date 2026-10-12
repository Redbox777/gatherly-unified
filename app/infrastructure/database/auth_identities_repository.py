"""Persistence operations for Gatherly users and login identities."""

from dataclasses import dataclass
from datetime import UTC, datetime
import sqlite3
from uuid import uuid4

from app.infrastructure.database.connection import transaction


@dataclass(frozen=True, slots=True)
class AuthIdentity:
    """A login identity linked to one internal Gatherly user."""

    identity_id: str
    user_id: str
    provider: str
    provider_subject: str
    password_hash: str | None
    verified_at: datetime | None
    created_at: datetime


class AuthIdentitiesRepository:
    """Create and look up identities without implicitly merging accounts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection for related application-level operations."""
        return self._connection

    def create_user_with_identity(
        self,
        *,
        provider: str,
        provider_subject: str,
        now: datetime,
        password_hash: str | None = None,
        verified_at: datetime | None = None,
    ) -> AuthIdentity:
        """Atomically create a user and their first login identity."""
        self._validate_identity(
            provider=provider,
            provider_subject=provider_subject,
            password_hash=password_hash,
            now=now,
            verified_at=verified_at,
        )
        user_id = str(uuid4())
        identity_id = str(uuid4())
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO users(id, created_at) VALUES (?, ?)",
                (user_id, self._format(now)),
            )
            self._connection.execute(
                """INSERT INTO auth_identities(
                    id, user_id, provider, provider_subject, password_hash,
                    verified_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    identity_id,
                    user_id,
                    provider,
                    provider_subject,
                    password_hash,
                    self._format(verified_at) if verified_at else None,
                    self._format(now),
                ),
            )
        return AuthIdentity(
            identity_id=identity_id,
            user_id=user_id,
            provider=provider,
            provider_subject=provider_subject,
            password_hash=password_hash,
            verified_at=verified_at,
            created_at=now,
        )

    def find_by_provider_subject(
        self, *, provider: str, provider_subject: str
    ) -> AuthIdentity | None:
        """Find an exact provider identity; never merge users implicitly."""
        row = self._connection.execute(
            """SELECT id, user_id, provider, provider_subject, password_hash,
                      verified_at, created_at
               FROM auth_identities
               WHERE provider = ? AND provider_subject = ?""",
            (provider, provider_subject),
        ).fetchone()
        if row is None:
            return None
        created_at = self._parse(row["created_at"])
        if created_at is None:
            raise ValueError("Identity creation timestamp is missing")
        return AuthIdentity(
            identity_id=str(row["id"]),
            user_id=str(row["user_id"]),
            provider=str(row["provider"]),
            provider_subject=str(row["provider_subject"]),
            password_hash=row["password_hash"],
            verified_at=self._parse(row["verified_at"]),
            created_at=created_at,
        )

    @classmethod
    def _validate_identity(
        cls,
        *,
        provider: str,
        provider_subject: str,
        password_hash: str | None,
        now: datetime,
        verified_at: datetime | None,
    ) -> None:
        cls._require_aware(now)
        if verified_at is not None:
            cls._require_aware(verified_at)
            if verified_at > now:
                raise ValueError("Verification time cannot be in the future")
        if provider not in {"email", "telegram"}:
            raise ValueError("Unsupported authentication provider")
        if not provider_subject.strip():
            raise ValueError("Provider subject must not be blank")
        if provider == "email" and (
            password_hash is None or not password_hash.strip()
        ):
            raise ValueError("Email identities require a password hash")
        if provider == "telegram" and password_hash is not None:
            raise ValueError("Telegram identities cannot store a password hash")

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

    @classmethod
    def _parse(cls, value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        cls._require_aware(parsed)
        return parsed

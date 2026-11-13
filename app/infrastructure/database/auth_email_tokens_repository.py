"""Persistence operations for one-time email verification and reset tokens."""

from datetime import UTC, datetime
import sqlite3
from uuid import uuid4

from app.domain.auth_tokens import hash_token
from app.infrastructure.database.connection import transaction


class AuthEmailTokensRepository:
    """Persist token digests and consume email tokens atomically."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_verification_token(
        self,
        *,
        user_id: str,
        token_digest: str,
        now: datetime,
        expires_at: datetime,
    ) -> str:
        """Store and immediately activate a verification token."""
        return self._create_token(
            user_id=user_id,
            token_digest=token_digest,
            now=now,
            expires_at=expires_at,
            purpose="verify_email",
            require_verified=False,
            activate_immediately=True,
        )

    def create_password_reset_token(
        self,
        *,
        user_id: str,
        token_digest: str,
        now: datetime,
        expires_at: datetime,
    ) -> str:
        """Store and immediately activate a password-reset token."""
        return self._create_token(
            user_id=user_id,
            token_digest=token_digest,
            now=now,
            expires_at=expires_at,
            purpose="reset_password",
            require_verified=True,
            activate_immediately=True,
        )

    def create_pending_verification_token(
        self,
        *,
        user_id: str,
        token_digest: str,
        now: datetime,
        expires_at: datetime,
    ) -> str:
        """Store a verification token without replacing the current usable token."""
        return self._create_token(
            user_id=user_id,
            token_digest=token_digest,
            now=now,
            expires_at=expires_at,
            purpose="verify_email",
            require_verified=False,
            activate_immediately=False,
        )

    def create_pending_password_reset_token(
        self,
        *,
        user_id: str,
        token_digest: str,
        now: datetime,
        expires_at: datetime,
    ) -> str:
        """Store a reset token without replacing the current usable token."""
        return self._create_token(
            user_id=user_id,
            token_digest=token_digest,
            now=now,
            expires_at=expires_at,
            purpose="reset_password",
            require_verified=True,
            activate_immediately=False,
        )

    def _create_token(
        self,
        *,
        user_id: str,
        token_digest: str,
        now: datetime,
        expires_at: datetime,
        purpose: str,
        require_verified: bool,
        activate_immediately: bool,
    ) -> str:
        self._validate_times(now=now, expires_at=expires_at)
        if not user_id.strip():
            raise ValueError("User ID must not be blank")
        if not token_digest.strip():
            raise ValueError("Token digest must not be blank")
        token_id = str(uuid4())
        verification_clause = (
            "verified_at IS NOT NULL" if require_verified else "verified_at IS NULL"
        )
        now_text = self._format(now)
        with transaction(self._connection):
            identity = self._connection.execute(
                f"""SELECT 1 FROM auth_identities
                    WHERE user_id = ? AND provider = 'email'
                      AND {verification_clause}""",
                (user_id,),
            ).fetchone()
            if identity is None:
                requirement = "verified" if require_verified else "unverified"
                raise ValueError(f"A {requirement} email identity is required")
            if activate_immediately:
                self._connection.execute(
                    """UPDATE auth_email_tokens SET consumed_at = ?
                       WHERE user_id = ? AND purpose = ?
                         AND activated_at IS NOT NULL AND consumed_at IS NULL""",
                    (now_text, user_id, purpose),
                )
            self._connection.execute(
                """INSERT INTO auth_email_tokens(
                    id, user_id, token_hash, purpose, created_at, expires_at,
                    consumed_at, activated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    token_id,
                    user_id,
                    token_digest,
                    purpose,
                    now_text,
                    self._format(expires_at),
                    now_text if activate_immediately else None,
                ),
            )
        return token_id

    def activate_pending_token(
        self, *, token_id: str, purpose: str, now: datetime
    ) -> bool:
        """Atomically make a delivered token current and retire older active tokens."""
        self._require_aware(now)
        if purpose not in {"verify_email", "reset_password"}:
            raise ValueError("Unsupported email-token purpose")
        now_text = self._format(now)
        with transaction(self._connection):
            row = self._connection.execute(
                """SELECT user_id FROM auth_email_tokens
                   WHERE id = ? AND purpose = ? AND activated_at IS NULL
                     AND consumed_at IS NULL AND expires_at > ?""",
                (token_id, purpose, now_text),
            ).fetchone()
            if row is None:
                return False
            user_id = str(row["user_id"])
            identity = self._connection.execute(
                """SELECT verified_at FROM auth_identities
                   WHERE user_id = ? AND provider = 'email'""",
                (user_id,),
            ).fetchone()
            if identity is None or (
                (purpose == "reset_password") != (identity["verified_at"] is not None)
            ):
                return False
            self._connection.execute(
                """UPDATE auth_email_tokens SET consumed_at = ?
                   WHERE user_id = ? AND purpose = ? AND id != ?
                     AND activated_at IS NOT NULL AND consumed_at IS NULL""",
                (now_text, user_id, purpose, token_id),
            )
            cursor = self._connection.execute(
                """UPDATE auth_email_tokens SET activated_at = ?
                   WHERE id = ? AND purpose = ? AND activated_at IS NULL
                     AND consumed_at IS NULL AND expires_at > ?""",
                (now_text, token_id, purpose, now_text),
            )
            return cursor.rowcount == 1

    def discard_pending_token(
        self, *, token_id: str, purpose: str, now: datetime
    ) -> bool:
        """Invalidate a staged token after its delivery attempt fails."""
        self._require_aware(now)
        if purpose not in {"verify_email", "reset_password"}:
            raise ValueError("Unsupported email-token purpose")
        with transaction(self._connection):
            cursor = self._connection.execute(
                """UPDATE auth_email_tokens SET consumed_at = ?
                   WHERE id = ? AND purpose = ? AND activated_at IS NULL
                     AND consumed_at IS NULL""",
                (self._format(now), token_id, purpose),
            )
        return cursor.rowcount == 1

    def consume_verification_token(self, *, raw_token: str, now: datetime) -> bool:
        """Consume an active valid token once and mark the user's email identity verified."""
        self._require_aware(now)
        digest = hash_token(raw_token)
        with transaction(self._connection):
            row = self._connection.execute(
                """SELECT id, user_id FROM auth_email_tokens
                   WHERE token_hash = ? AND purpose = 'verify_email'
                     AND activated_at IS NOT NULL
                     AND consumed_at IS NULL AND expires_at > ?""",
                (digest, self._format(now)),
            ).fetchone()
            if row is None:
                return False
            cursor = self._connection.execute(
                """UPDATE auth_email_tokens SET consumed_at = ?
                   WHERE id = ? AND consumed_at IS NULL AND expires_at > ?""",
                (self._format(now), str(row["id"]), self._format(now)),
            )
            if cursor.rowcount != 1:
                return False
            identity = self._connection.execute(
                """UPDATE auth_identities SET verified_at = ?
                   WHERE user_id = ? AND provider = 'email'""",
                (self._format(now), str(row["user_id"])),
            )
            if identity.rowcount != 1:
                raise RuntimeError("Verification token has no email identity")
        return True

    def consume_password_reset_token(
        self, *, raw_token: str, password_hash: str, now: datetime
    ) -> bool:
        """Consume an active reset token, replace the password, and revoke all sessions."""
        self._require_aware(now)
        if not password_hash.strip():
            raise ValueError("Password hash must not be blank")
        digest = hash_token(raw_token)
        with transaction(self._connection):
            row = self._connection.execute(
                """SELECT id, user_id FROM auth_email_tokens
                   WHERE token_hash = ? AND purpose = 'reset_password'
                     AND activated_at IS NOT NULL
                     AND consumed_at IS NULL AND expires_at > ?""",
                (digest, self._format(now)),
            ).fetchone()
            if row is None:
                return False
            user_id = str(row["user_id"])
            cursor = self._connection.execute(
                """UPDATE auth_email_tokens SET consumed_at = ?
                   WHERE id = ? AND consumed_at IS NULL AND expires_at > ?""",
                (self._format(now), str(row["id"]), self._format(now)),
            )
            if cursor.rowcount != 1:
                return False
            identity = self._connection.execute(
                """UPDATE auth_identities SET password_hash = ?
                   WHERE user_id = ? AND provider = 'email'
                     AND verified_at IS NOT NULL""",
                (password_hash, user_id),
            )
            if identity.rowcount != 1:
                raise RuntimeError("Reset token has no verified email identity")
            self._connection.execute(
                """UPDATE auth_sessions SET revoked_at = ?
                   WHERE user_id = ? AND revoked_at IS NULL""",
                (self._format(now), user_id),
            )
        return True

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")

    @classmethod
    def _validate_times(cls, *, now: datetime, expires_at: datetime) -> None:
        cls._require_aware(now)
        cls._require_aware(expires_at)
        if expires_at <= now:
            raise ValueError("Token expiry must be after creation")

    @classmethod
    def _format(cls, value: datetime) -> str:
        cls._require_aware(value)
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )

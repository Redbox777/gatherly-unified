"""Application service for secure password-reset token lifecycle."""

from datetime import datetime, timedelta

from app.domain.auth_tokens import issue_token
from app.domain.passwords import hash_password
from app.infrastructure.database.auth_email_tokens_repository import (
    AuthEmailTokensRepository,
)


class PasswordResetService:
    """Issue short-lived reset tokens and replace passwords after one-time use."""

    def __init__(
        self,
        repository: AuthEmailTokensRepository,
        *,
        lifetime: timedelta = timedelta(hours=1),
    ) -> None:
        if lifetime <= timedelta(0):
            raise ValueError("Password reset token lifetime must be positive")
        self._repository = repository
        self._lifetime = lifetime

    def issue(self, *, user_id: str, now: datetime) -> str:
        """Create a reset token; return the raw token only to the caller."""
        self._require_aware(now)
        issued = issue_token()
        self._repository.create_password_reset_token(
            user_id=user_id,
            token_digest=issued.digest,
            now=now,
            expires_at=now + self._lifetime,
        )
        return issued.raw

    def reset_password(
        self, *, raw_token: str, new_password: str, now: datetime
    ) -> bool:
        """Set a new password only when the token is valid, current, and unused."""
        self._require_aware(now)
        if not raw_token or not raw_token.strip():
            return False
        encoded_password = hash_password(new_password)
        return self._repository.consume_password_reset_token(
            raw_token=raw_token,
            password_hash=encoded_password,
            now=now,
        )

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")

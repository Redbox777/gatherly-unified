"""Application service for email-verification token lifecycle."""

from datetime import datetime, timedelta

from app.domain.auth_tokens import issue_token
from app.infrastructure.database.auth_email_tokens_repository import (
    AuthEmailTokensRepository,
)


class EmailVerificationService:
    """Issue short-lived verification tokens and consume them exactly once."""

    def __init__(
        self,
        repository: AuthEmailTokensRepository,
        *,
        lifetime: timedelta = timedelta(hours=24),
    ) -> None:
        if lifetime <= timedelta(0):
            raise ValueError("Verification token lifetime must be positive")
        self._repository = repository
        self._lifetime = lifetime

    def issue(self, *, user_id: str, now: datetime) -> str:
        """Create a verification token; return the raw token only to the caller."""
        self._require_aware(now)
        issued = issue_token()
        self._repository.create_verification_token(
            user_id=user_id,
            token_digest=issued.digest,
            now=now,
            expires_at=now + self._lifetime,
        )
        return issued.raw

    def verify(self, *, raw_token: str, now: datetime) -> bool:
        """Verify and consume a token; invalid, expired, or reused tokens return False."""
        self._require_aware(now)
        if not raw_token or not raw_token.strip():
            return False
        return self._repository.consume_verification_token(raw_token=raw_token, now=now)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")

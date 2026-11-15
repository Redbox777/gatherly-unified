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

    def issue(self, *, identity_id: str, now: datetime) -> str:
        """Create a verification token; return the raw token only to the caller."""
        self._require_aware(now)
        issued = issue_token()
        self._repository.create_verification_token(
            identity_id=identity_id,
            token_digest=issued.digest,
            now=now,
            expires_at=now + self._lifetime,
        )
        return issued.raw

    def issue_pending(self, *, identity_id: str, now: datetime) -> tuple[str, str]:
        """Stage a token while keeping the previously delivered token usable."""
        self._require_aware(now)
        issued = issue_token()
        token_id = self._repository.create_pending_verification_token(
            identity_id=identity_id,
            token_digest=issued.digest,
            now=now,
            expires_at=now + self._lifetime,
        )
        return issued.raw, token_id

    def activate_pending(self, *, token_id: str, now: datetime) -> bool:
        """Activate a staged token only after its email was accepted for delivery."""
        self._require_aware(now)
        return self._repository.activate_pending_token(
            token_id=token_id, purpose="verify_email", now=now
        )

    def discard_pending(self, *, token_id: str, now: datetime) -> bool:
        """Discard a staged token after an unsuccessful delivery attempt."""
        self._require_aware(now)
        return self._repository.discard_pending_token(
            token_id=token_id, purpose="verify_email", now=now
        )

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

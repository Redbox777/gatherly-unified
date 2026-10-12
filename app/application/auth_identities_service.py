"""Application-level account creation and identity lookup."""

from datetime import datetime

from app.infrastructure.database.auth_identities_repository import (
    AuthIdentitiesRepository,
    AuthIdentity,
)


class AuthIdentitiesService:
    """Expose explicit account-creation flows without implicit account linking."""

    def __init__(self, repository: AuthIdentitiesRepository) -> None:
        self._repository = repository

    def register_email(
        self,
        *,
        email: str,
        password_hash: str,
        now: datetime,
    ) -> AuthIdentity:
        """Create an unverified email account using an already-derived password hash."""
        normalized_email = email.strip().casefold()
        if not normalized_email or "@" not in normalized_email:
            raise ValueError("A valid email address is required")
        if not password_hash.strip():
            raise ValueError("Password hash must not be blank")
        return self._repository.create_user_with_identity(
            provider="email",
            provider_subject=normalized_email,
            password_hash=password_hash,
            now=now,
        )

    def register_telegram(
        self,
        *,
        telegram_subject: str,
        now: datetime,
        verified_at: datetime,
    ) -> AuthIdentity:
        """Create a Telegram account after the adapter verifies Telegram's payload."""
        if not telegram_subject.strip():
            raise ValueError("Telegram subject must not be blank")
        return self._repository.create_user_with_identity(
            provider="telegram",
            provider_subject=telegram_subject,
            now=now,
            verified_at=verified_at,
        )

    def find_identity(
        self, *, provider: str, provider_subject: str
    ) -> AuthIdentity | None:
        """Look up an identity without attempting to link or merge accounts."""
        return self._repository.find_by_provider_subject(
            provider=provider,
            provider_subject=provider_subject,
        )

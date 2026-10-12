"""Application-level orchestration for authentication sessions."""

from datetime import datetime, timedelta

from app.infrastructure.database.auth_sessions_repository import (
    AuthSessionsRepository,
    CreatedSession,
)


class AuthSessionsService:
    """Apply session lifetime policy while leaving persistence to the repository."""

    def __init__(
        self,
        repository: AuthSessionsRepository,
        *,
        lifetime: timedelta = timedelta(days=14),
    ) -> None:
        if lifetime <= timedelta(0):
            raise ValueError("Session lifetime must be positive")
        self._repository = repository
        self._lifetime = lifetime

    def create(self, *, user_id: str, now: datetime) -> CreatedSession:
        """Issue a session with the configured lifetime."""
        return self._repository.create(
            user_id=user_id,
            now=now,
            expires_at=now + self._lifetime,
        )

    def resolve(self, *, raw_token: str, now: datetime) -> str | None:
        """Resolve an active session token to its internal user ID."""
        return self._repository.get_active_user_id(raw_token=raw_token, now=now)

    def revoke(self, *, raw_token: str, now: datetime) -> bool:
        """Invalidate an active session token."""
        return self._repository.revoke(raw_token=raw_token, now=now)

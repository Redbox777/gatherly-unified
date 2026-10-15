"""Coordinate credential authentication with server-side session issuance."""

from datetime import datetime
import sqlite3

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.auth_sessions_service import AuthSessionsService
from app.infrastructure.database.auth_sessions_repository import CreatedSession


class AuthenticationService:
    """Authenticate credentials and issue a session only after successful login."""

    def __init__(
        self,
        identities: AuthIdentitiesService,
        sessions: AuthSessionsService,
    ) -> None:
        self._identities = identities
        self._sessions = sessions

    def login_email(
        self, *, email: str, password: str, now: datetime
    ) -> CreatedSession | None:
        """Return a new session for a verified email identity, else None."""
        identity = self._identities.authenticate_email(email=email, password=password)
        if identity is None:
            return None
        return self._sessions.create(user_id=identity.user_id, now=now)

    def login_telegram(
        self, *, telegram_subject: str, now: datetime, verified_at: datetime
    ) -> CreatedSession:
        """Sign in an existing Telegram identity or create its first account."""
        identity = self._identities.find_identity(
            provider="telegram", provider_subject=telegram_subject
        )
        if identity is None:
            try:
                identity = self._identities.register_telegram(
                    telegram_subject=telegram_subject,
                    now=now,
                    verified_at=verified_at,
                )
            except sqlite3.IntegrityError:
                # Another request may have created this identity concurrently.
                identity = self._identities.find_identity(
                    provider="telegram", provider_subject=telegram_subject
                )
                if identity is None:
                    raise
        return self._sessions.create(user_id=identity.user_id, now=now)

    def resolve_session(self, *, raw_token: str, now: datetime) -> str | None:
        """Return the user ID for an active session token."""
        return self._sessions.resolve(raw_token=raw_token, now=now)

    def logout(self, *, raw_token: str, now: datetime) -> bool:
        """Revoke the supplied session token."""
        return self._sessions.revoke(raw_token=raw_token, now=now)

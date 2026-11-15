"""Coordinate password-reset requests without exposing account existence."""

from datetime import datetime
import logging
from typing import Protocol
from urllib.parse import urlencode

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.password_reset_service import PasswordResetService


class EmailSender(Protocol):
    """Minimal port implemented by the SMTP infrastructure adapter."""

    def send_text(self, *, recipient: str, subject: str, body: str) -> None:
        """Deliver a plain-text message."""


logger = logging.getLogger(__name__)


GENERIC_RESPONSE = (
    "If an eligible account exists for that email address, "
    "password-reset instructions will be sent."
)


class PasswordResetRequestService:
    """Issue reset email only for verified accounts with a generic public response."""

    def __init__(
        self,
        *,
        identities: AuthIdentitiesService,
        resets: PasswordResetService,
        email_sender: EmailSender,
        reset_url: str,
    ) -> None:
        if not reset_url.startswith(("https://", "http://")):
            raise ValueError("Reset URL must be an absolute HTTP(S) URL")
        self._identities = identities
        self._resets = resets
        self._email_sender = email_sender
        self._reset_url = reset_url

    def request(self, *, email: str, now: datetime) -> str:
        """Always return the same public message for known and unknown addresses."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")
        identity = self._identities.find_identity(
            provider="email",
            provider_subject=email.strip().casefold(),
        )
        if (
            identity is None
            or identity.verified_at is None
            or not identity.provider_subject
        ):
            return GENERIC_RESPONSE

        token, token_id = self._resets.issue_pending(identity_id=identity.identity_id, now=now)
        link = f"{self._reset_url.split('#', maxsplit=1)[0]}#{urlencode({'token': token})}"
        try:
            self._email_sender.send_text(
                recipient=identity.provider_subject,
                subject="Reset your Gatherly password",
                body=(
                    "A password reset was requested for your Gatherly account.\n\n"
                    f"Use this link within one hour:\n{link}\n\n"
                    "If you did not request this, you can ignore this email."
                ),
            )
        except Exception as error:
            # A failed send must not invalidate a previously delivered reset link.
            try:
                self._resets.discard_pending(token_id=token_id, now=now)
            except Exception as cleanup_error:
                logger.error(
                    "Failed to discard pending password reset token (%s)",
                    type(cleanup_error).__name__,
                )
            logger.warning(
                "Password reset email delivery failed (%s)",
                type(error).__name__,
            )
            return GENERIC_RESPONSE

        if not self._resets.activate_pending(token_id=token_id, now=now):
            logger.error("Password reset token activation failed after email delivery")
        return GENERIC_RESPONSE

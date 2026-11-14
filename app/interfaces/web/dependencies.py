"""Request-scoped service composition for the HTTP adapter."""

import os
import sqlite3
from collections.abc import Callable

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.auth_sessions_service import AuthSessionsService
from app.application.authentication_service import AuthenticationService
from app.application.email_verification_service import EmailVerificationService
from app.application.events import EventsService
from app.application.participants import ParticipantsService
from app.application.participant_invitations import ParticipantInvitationsService
from app.application.password_reset_request_service import PasswordResetRequestService
from app.application.password_reset_service import PasswordResetService
from app.infrastructure.database.auth_email_tokens_repository import AuthEmailTokensRepository
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.events_repository import EventsRepository
from app.infrastructure.database.event_tasks_repository import EventTasksRepository
from app.infrastructure.database.event_expenses_repository import EventExpensesRepository
from app.infrastructure.database.event_expense_splits_repository import EventExpenseSplitsRepository
from app.infrastructure.database.event_finance_repository import EventFinanceRepository
from app.infrastructure.database.event_venues_repository import EventVenuesRepository
from app.infrastructure.database.notes_repository import NotesRepository
from app.infrastructure.database.notifications_repository import NotificationsRepository
from app.infrastructure.database.participant_invitations_repository import (
    ParticipantInvitationsRepository,
)
from app.infrastructure.database.participants_repository import ParticipantsRepository
from app.infrastructure.database.smtp_rate_limits_repository import SmtpRateLimitsRepository
from app.infrastructure.email.smtp_email_sender import SmtpEmailSender
from app.interfaces.web.runtime_config import SmtpRateLimitSettings


def _email_sender() -> SmtpEmailSender:
    """Build the SMTP adapter from environment configuration."""
    return SmtpEmailSender(
        host=os.environ.get("SMTP_HOST", "localhost"),
        port=int(os.environ.get("SMTP_PORT", "587")),
        username=os.environ.get("SMTP_USERNAME", ""),
        password=os.environ.get("SMTP_PASSWORD", ""),
        sender=os.environ.get("SMTP_SENDER", "no-reply@gatherly.local"),
        use_starttls=os.environ.get("SMTP_STARTTLS", "true").casefold() == "true",
        timeout=float(os.environ.get("SMTP_TIMEOUT", "10")),
    )


class Dependencies:
    """Construct request-scoped services around a database connection and mail adapter."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        smtp_rate_limit_settings: SmtpRateLimitSettings,
        email_sender_factory: Callable[[], SmtpEmailSender] | None = None,
    ) -> None:
        self.connection = connection
        self.smtp_rate_limits = SmtpRateLimitsRepository(
            connection,
            recipient_limit=smtp_rate_limit_settings.recipient_limit,
            recipient_window_seconds=smtp_rate_limit_settings.recipient_window_seconds,
            requester_limit=smtp_rate_limit_settings.requester_limit,
            requester_window_seconds=smtp_rate_limit_settings.requester_window_seconds,
        )
        identity_repository = AuthIdentitiesRepository(connection)
        token_repository = AuthEmailTokensRepository(connection)
        session_repository = AuthSessionsRepository(connection)
        self.identities = AuthIdentitiesService(identity_repository)
        self.verification = EmailVerificationService(token_repository)
        self.resets = PasswordResetService(token_repository)
        self.sessions = AuthSessionsService(session_repository)
        self.authentication = AuthenticationService(self.identities, self.sessions)
        self.events = EventsService(EventsRepository(connection))
        participant_repository = ParticipantsRepository(connection)
        self.participants = ParticipantsService(self.events, participant_repository)
        self.participant_invitations_repository = ParticipantInvitationsRepository(
            connection
        )
        self.participant_invitations = ParticipantInvitationsService(
            events=self.events,
            participants=participant_repository,
            invitations=self.participant_invitations_repository,
        )
        self.email_sender = (email_sender_factory or _email_sender)()
        self.notifications = NotificationsRepository(connection)
        self.notes = NotesRepository(connection)
        self.event_tasks = EventTasksRepository(connection)
        self.event_venues = EventVenuesRepository(connection)
        self.event_expenses = EventExpensesRepository(connection)
        self.event_expense_splits = EventExpenseSplitsRepository(connection)
        self.event_finance = EventFinanceRepository(connection)
        self.reset_requests = PasswordResetRequestService(
            identities=self.identities,
            resets=self.resets,
            email_sender=self.email_sender,
            reset_url=os.environ.get(
                "PASSWORD_RESET_URL", "http://localhost:8000/reset-password"
            ),
        )

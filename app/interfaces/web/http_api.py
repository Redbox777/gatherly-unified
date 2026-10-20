"""HTTP adapter for email authentication and server-managed sessions."""

from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID
import hashlib
import logging
import os
from pathlib import Path
import sqlite3
from threading import Lock
from time import monotonic
from typing import Annotated, cast
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.auth_sessions_service import AuthSessionsService
from app.application.authentication_service import AuthenticationService
from app.application.email_verification_service import EmailVerificationService
from app.application.events import EventNotFoundError, EventsService
from app.application.participants import DuplicateParticipantError, ParticipantsService
from app.application.participant_invitations import (
    ParticipantAlreadyLinkedError,
    ParticipantInvitationsService,
)
from app.application.password_reset_request_service import PasswordResetRequestService
from app.application.password_reset_service import PasswordResetService
from app.infrastructure.database.auth_email_tokens_repository import (
    AuthEmailTokensRepository,
)
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.events_repository import EventsRepository
from app.infrastructure.database.participants_repository import ParticipantsRepository
from app.infrastructure.database.participant_invitations_repository import (
    ParticipantInvitationsRepository,
)
from app.infrastructure.email.smtp_email_sender import SmtpEmailSender
from app.interfaces.telegram.login_verification import verify_telegram_login


logger = logging.getLogger(__name__)

SESSION_COOKIE = "gatherly_session"
SESSION_COOKIE_PATH = "/"
GENERIC_RESET_RESPONSE = {
    "message": (
        "If an eligible account exists for that email address, "
        "password-reset instructions will be sent."
    )
}


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class CreateEventRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime | None = None


class UpdateEventRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime | None = None


class AddParticipantRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class CreateParticipantInvitationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class AcceptParticipantInvitationRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class TelegramLoginRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=32)
    auth_date: str = Field(min_length=1, max_length=16)
    hash: str = Field(min_length=64, max_length=64)


class EmailTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=1024)


class Dependencies:
    """Construct application services around one database connection and mail adapter."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
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
        self.email_sender = _email_sender()
        self.reset_requests = PasswordResetRequestService(
            identities=self.identities,
            resets=self.resets,
            email_sender=self.email_sender,
            reset_url=os.environ.get(
                "PASSWORD_RESET_URL", "http://localhost:8000/reset-password"
            ),
        )


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


def create_app(database_path: str | Path | None = None) -> FastAPI:
    """Create an API app; tests may inject a temporary SQLite database path."""
    app = FastAPI(title="Gatherly Authentication API", version="0.1.0")
    rate_limit_window_seconds = 60.0
    rate_limit_max_requests = 5
    rate_limit_paths = {
        "/auth/login",
        "/auth/telegram/login",
        "/auth/register",
        "/auth/password-reset/request",
        "/auth/password-reset/confirm",
        "/auth/password/change",
        "/auth/verify-email",
        "/participant-invitations/accept",
    }
    # Bound memory if requests arrive from many distinct client addresses.
    rate_limit_max_tracked_keys = 10_000
    request_times: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
    request_times_lock = Lock()

    @app.middleware("http")
    async def validate_browser_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method == "POST" and request.url.path in rate_limit_paths:
            client_host = request.client.host if request.client else "unknown"
            key = (client_host, request.url.path)
            current_time = monotonic()
            with request_times_lock:
                timestamps = request_times.get(key)
                if timestamps is None and len(request_times) >= rate_limit_max_tracked_keys:
                    # Reclaim expired entries before applying the bounded fallback.
                    for tracked_key, tracked_times in list(request_times.items()):
                        while (
                            tracked_times
                            and current_time - tracked_times[0] >= rate_limit_window_seconds
                        ):
                            tracked_times.popleft()
                        if not tracked_times:
                            del request_times[tracked_key]
                    timestamps = request_times.get(key)
                    if timestamps is None:
                        # Never evict an active client's counter to grant a fresh quota.
                        # Under key exhaustion, new clients share a per-route overflow bucket.
                        key = ("\\x00overflow", request.url.path)
                        timestamps = request_times.get(key)
                        if timestamps is None:
                            if len(request_times) >= rate_limit_max_tracked_keys:
                                request_times.popitem(last=False)
                            timestamps = deque()
                            request_times[key] = timestamps
                elif timestamps is None:
                    timestamps = deque()
                    request_times[key] = timestamps
                request_times.move_to_end(key)
                while (
                    timestamps
                    and current_time - timestamps[0] >= rate_limit_window_seconds
                ):
                    timestamps.popleft()
                if len(timestamps) >= rate_limit_max_requests:
                    return Response(
                        content='{"detail":"Too many requests; try again later"}',
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        headers={"Retry-After": str(int(rate_limit_window_seconds))},
                        media_type="application/json",
                    )
                timestamps.append(current_time)

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                origin_parts = urlsplit(origin)
                request_parts = urlsplit(str(request.base_url))
                if (
                    origin_parts.scheme not in {"http", "https"}
                    or origin_parts.netloc != request_parts.netloc
                ):
                    return Response(
                        content='{"detail":"Cross-origin request rejected"}',
                        status_code=status.HTTP_403_FORBIDDEN,
                        media_type="application/json",
                    )
        return await call_next(request)
    path = database_path or os.environ.get("GATHERLY_DATABASE_PATH", "var/gatherly.sqlite3")
    connection = connect(path)
    migrate(connection)
    dependencies = Dependencies(connection)
    app.state.auth_dependencies = dependencies

    @app.on_event("shutdown")
    def close_database() -> None:
        connection.close()

    def services(request: Request) -> Dependencies:
        return cast(Dependencies, request.app.state.auth_dependencies)

    ServiceDependency = Annotated[Dependencies, Depends(services)]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/register", status_code=status.HTTP_202_ACCEPTED)
    def register(payload: RegisterRequest, deps: ServiceDependency) -> dict[str, str]:
        now = datetime.now(UTC)
        try:
            identity = deps.identities.register_email(
                email=payload.email, password=payload.password, now=now
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid registration data") from error
        except sqlite3.IntegrityError as error:
            raise HTTPException(
                status_code=409, detail="An account may already exist for this email"
            ) from error
        token = deps.verification.issue(user_id=identity.user_id, now=now)
        verify_url = os.environ.get(
            "EMAIL_VERIFICATION_URL", "http://localhost:8000/auth/verify-email"
        )
        try:
            deps.email_sender.send_text(
                recipient=identity.provider_subject,
                subject="Verify your Gatherly email",
                body=(
                    "Confirm your email address using this one-time token:\n\n"
                    f"{verify_url}?token={token}\n\n"
                    "If you did not create this account, you can ignore this email."
                ),
            )
        except Exception:
            raise HTTPException(
                status_code=503, detail="Email delivery is temporarily unavailable"
            ) from None
        return {
            "message": (
                "If registration can be completed, verification instructions will be sent."
            )
        }

    @app.post("/auth/verify-email")
    def verify_email(payload: EmailTokenRequest, deps: ServiceDependency) -> dict[str, str]:
        if not deps.verification.verify(raw_token=payload.token, now=datetime.now(UTC)):
            raise HTTPException(status_code=400, detail="Invalid or expired verification token")
        return {"message": "Email verified"}

    @app.post("/auth/login")
    def login(payload: LoginRequest, response: Response, deps: ServiceDependency) -> dict[str, str]:
        session = deps.authentication.login_email(
            email=payload.email, password=payload.password, now=datetime.now(UTC)
        )
        if session is None:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session.raw_token,
            httponly=True,
            secure=os.environ.get("COOKIE_SECURE", "true").casefold() == "true",
            samesite="lax",
            path=SESSION_COOKIE_PATH,
            max_age=max(0, int((session.expires_at - session.created_at).total_seconds())),
        )
        return {"message": "Signed in"}

    @app.post("/auth/telegram/login")
    def login_telegram(
        payload: TelegramLoginRequest, response: Response, deps: ServiceDependency
    ) -> dict[str, str]:
        now = datetime.now(UTC)
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        telegram_payload = {
            key: value
            for key, value in payload.model_dump().items()
            if isinstance(value, str)
        }
        if not verify_telegram_login(
            payload=telegram_payload, bot_token=bot_token, now=now
        ):
            raise HTTPException(status_code=401, detail="Invalid Telegram login")
        verified_at = datetime.fromtimestamp(int(payload.auth_date), tz=UTC)
        signature_hash = hashlib.sha256(payload.hash.encode("ascii")).hexdigest()
        try:
            # Keep the one-time-use record only for the Login Widget validity
            # window, while the unique key makes concurrent replays atomic.
            deps.connection.execute(
                "DELETE FROM auth_telegram_login_payloads WHERE consumed_at < ?",
                (datetime.fromtimestamp(now.timestamp() - 600, tz=UTC).isoformat(),),
            )
            deps.connection.execute(
                "INSERT INTO auth_telegram_login_payloads(payload_hash, consumed_at) "
                "VALUES (?, ?)",
                (signature_hash, now.isoformat()),
            )
            deps.connection.commit()
        except sqlite3.IntegrityError:
            deps.connection.rollback()
            raise HTTPException(
                status_code=401, detail="Invalid Telegram login"
            ) from None
        try:
            session = deps.authentication.login_telegram(
                telegram_subject=payload.id, now=now, verified_at=verified_at
            )
        except ValueError as error:
            raise HTTPException(status_code=401, detail="Invalid Telegram login") from error
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session.raw_token,
            httponly=True,
            secure=os.environ.get("COOKIE_SECURE", "true").casefold() == "true",
            samesite="lax",
            path=SESSION_COOKIE_PATH,
            max_age=max(0, int((session.expires_at - session.created_at).total_seconds())),
        )
        return {"message": "Signed in with Telegram"}

    @app.post("/auth/logout")
    def logout(request: Request, response: Response, deps: ServiceDependency) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        deps.authentication.logout(raw_token=token, now=datetime.now(UTC))
        response.delete_cookie(
            key=SESSION_COOKIE,
            httponly=True,
            secure=os.environ.get("COOKIE_SECURE", "true").casefold() == "true",
            samesite="lax",
            path=SESSION_COOKIE_PATH,
        )
        return {"message": "Signed out"}

    @app.get("/auth/me")
    def current_user(request: Request, deps: ServiceDependency) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return {"user_id": user_id}

    @app.post("/auth/password/change")
    def change_password(
        payload: ChangePasswordRequest,
        request: Request,
        response: Response,
        deps: ServiceDependency,
    ) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        changed = deps.identities.change_email_password(
            user_id=user_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
            now=datetime.now(UTC),
        )
        if not changed:
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        response.delete_cookie(
            key=SESSION_COOKIE,
            httponly=True,
            secure=os.environ.get("COOKIE_SECURE", "true").casefold() == "true",
            samesite="lax",
            path=SESSION_COOKIE_PATH,
        )
        return {"message": "Password updated; sign in again"}


    @app.post("/events", status_code=status.HTTP_201_CREATED)
    def create_event(
        payload: CreateEventRequest, request: Request, deps: ServiceDependency
    ) -> dict[str, str | None]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            event = deps.events.create_event(
                owner_id=user_id,
                title=payload.title,
                starts_at=payload.starts_at,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid event data") from error
        return {
            "id": str(event.id),
            "title": event.title,
            "starts_at": event.starts_at.isoformat() if event.starts_at else None,
            "created_at": event.created_at.isoformat(),
        }

    @app.get("/events")
    def list_events(request: Request, deps: ServiceDependency) -> list[dict[str, str | None]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        events = deps.events.list_events(owner_id=user_id)
        return [
            {
                "id": str(event.id),
                "title": event.title,
                "starts_at": event.starts_at.isoformat() if event.starts_at else None,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]

    @app.get("/events/{event_id}")
    def get_event(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, str | None]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(raw_token=token, now=datetime.now(UTC))
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            event = deps.events.get_event(event_id=event_id, owner_id=user_id)
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        return {
            "id": str(event.id),
            "title": event.title,
            "starts_at": event.starts_at.isoformat() if event.starts_at else None,
            "created_at": event.created_at.isoformat(),
        }

    @app.patch("/events/{event_id}")
    def update_event(
        event_id: UUID,
        payload: UpdateEventRequest,
        request: Request,
        deps: ServiceDependency,
    ) -> dict[str, str | None]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            event = deps.events.update_event(
                event_id=event_id, owner_id=user_id,
                title=payload.title, starts_at=payload.starts_at,
            )
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid event data") from error
        return {
            "id": str(event.id), "title": event.title,
            "starts_at": event.starts_at.isoformat() if event.starts_at else None,
            "created_at": event.created_at.isoformat(),
        }

    @app.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_event(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            deps.events.delete_event(event_id=event_id, owner_id=user_id)
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/events/{event_id}/participants", status_code=status.HTTP_201_CREATED)
    def add_event_participant(
        event_id: UUID,
        payload: AddParticipantRequest,
        request: Request,
        deps: ServiceDependency,
    ) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            participant = deps.participants.add_participant(
                event_id=event_id,
                owner_id=user_id,
                display_name=payload.display_name,
            )
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        except DuplicateParticipantError as error:
            raise HTTPException(status_code=409, detail="Participant already exists") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid participant data") from error
        return {
            "id": str(participant.id),
            "event_id": str(participant.event_id),
            "display_name": participant.display_name,
            "added_at": participant.added_at.isoformat(),
        }

    @app.delete(
        "/events/{event_id}/participants/{participant_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def remove_event_participant(
        event_id: UUID,
        participant_id: UUID,
        request: Request,
        deps: ServiceDependency,
    ) -> Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            deps.participants.remove_participant(
                event_id=event_id, owner_id=user_id, participant_id=participant_id
            )
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Participant not found") from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/events/{event_id}/participants")
    def list_event_participants(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> list[dict[str, str]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            participants = deps.participants.list_participants(
                event_id=event_id, owner_id=user_id
            )
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        return [
            {
                "id": str(participant.id),
                "event_id": str(participant.event_id),
                "display_name": participant.display_name,
                "added_at": participant.added_at.isoformat(),
            }
            for participant in participants
        ]

    @app.post(
        "/events/{event_id}/participants/{participant_id}/invitation",
        status_code=status.HTTP_201_CREATED,
    )
    def create_participant_invitation(
        event_id: UUID,
        participant_id: UUID,
        payload: CreateParticipantInvitationRequest,
        request: Request,
        deps: ServiceDependency,
    ) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        now = datetime.now(UTC)
        try:
            created = deps.participant_invitations.create_invitation(
                event_id=event_id,
                participant_id=participant_id,
                owner_id=owner_id,
                target_email=payload.email,
                now=now,
            )
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Participant not found") from error
        except ParticipantAlreadyLinkedError as error:
            raise HTTPException(status_code=409, detail="Participant is already linked") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid invitation data") from error

        invitation_url = os.environ.get(
            "PARTICIPANT_INVITATION_URL", "http://localhost:8000/accept-invitation"
        )
        # Put the bearer token in the fragment so it is not sent in the HTTP request URL.
        link = f"{invitation_url}#{urlencode({'token': created.raw_token})}"
        try:
            deps.email_sender.send_text(
                recipient=created.invitation.target_email,
                subject="You have a Gatherly participant invitation",
                body=(
                    "An event owner invited you to link your Gatherly account to an "
                    "event participant record.\n\n"
                    f"Sign in to Gatherly and open this link to accept the invitation:\n{link}\n\n"
                    "The link expires in seven days and can be used only once."
                ),
            )
        except Exception as error:
            # Keep operational visibility without logging recipient, token, or message.
            logger.warning(
                "Invitation email delivery failed (%s)", type(error).__name__
            )
            # A failed delivery must not leave the newly issued token usable.
            try:
                revoked = deps.participant_invitations.revoke_invitation(
                    event_id=event_id,
                    invitation_id=created.invitation.id,
                    owner_id=owner_id,
                    now=datetime.now(UTC),
                )
            except Exception:
                revoked = False
            if not revoked:
                raise HTTPException(
                    status_code=500,
                    detail="Invitation delivery failed and invitation state could not be confirmed",
                ) from None
            raise HTTPException(
                status_code=503, detail="Invitation email delivery is unavailable"
            ) from None
        return {
            "id": str(created.invitation.id),
            "expires_at": created.invitation.expires_at.isoformat(),
            "message": "Invitation sent",
        }

    @app.delete(
        "/events/{event_id}/participant-invitations/{invitation_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def revoke_participant_invitation(
        event_id: UUID,
        invitation_id: UUID,
        request: Request,
        deps: ServiceDependency,
    ) -> Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            revoked = deps.participant_invitations.revoke_invitation(
                event_id=event_id,
                invitation_id=invitation_id,
                owner_id=owner_id,
                now=datetime.now(UTC),
            )
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        if not revoked:
            raise HTTPException(status_code=404, detail="Invitation not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/accept-invitation", response_class=HTMLResponse)
    def accept_invitation_page() -> HTMLResponse:
        """Serve a minimal same-origin page for signing in and accepting an invite."""
        return HTMLResponse(
            content="""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Accept Gatherly invitation</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
      padding: 1rem; }
    main { width: min(100%, 28rem); box-sizing: border-box; padding: 1.5rem;
      border: 1px solid #8886; border-radius: 1rem; }
    h1 { font-size: 1.5rem; margin-top: 0; }
    label { display: block; margin-top: 1rem; }
    input, button { box-sizing: border-box; width: 100%; padding: .75rem;
      margin-top: .35rem; font: inherit; }
    button { margin-top: 1.25rem; cursor: pointer; }
    #status { min-height: 1.5em; overflow-wrap: anywhere; }
  </style>
</head>
<body>
  <main>
    <h1>Accept your Gatherly invitation</h1>
    <p>Sign in to the account whose verified email received this invitation.</p>
    <form id="accept-form">
      <div id="credentials">
        <label for="email">Email
          <input id="email" name="email" type="email"
            autocomplete="username" required maxlength="254">
        </label>
        <label for="password">Password
          <input id="password" name="password" type="password"
            autocomplete="current-password" required maxlength="1024">
        </label>
      </div>
      <button id="submit" type="submit">Sign in and accept invitation</button>
    </form>
    <p id="status" role="status" aria-live="polite"></p>
  </main>
  <script>
    (() => {
      const token = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
      window.history.replaceState({}, document.title, window.location.pathname);
      const form = document.getElementById("accept-form");
      const credentials = document.getElementById("credentials");
      const email = document.getElementById("email");
      const password = document.getElementById("password");
      const button = document.getElementById("submit");
      const status = document.getElementById("status");
      let authenticated = false;

      const showStatus = (message) => { status.textContent = message; };
      fetch("/auth/me", { credentials: "same-origin" })
        .then((response) => {
          if (response.ok) {
            authenticated = true;
            credentials.hidden = true;
            email.disabled = true;
            password.disabled = true;
            button.textContent = "Accept invitation";
          }
        })
        .catch(() => {});

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!token) {
          showStatus("This invitation link is missing its token. " +
            "Ask the event owner for a new invitation.");
          return;
        }
        button.disabled = true;
        try {
          if (!authenticated) {
            const login = await fetch("/auth/login", {
              method: "POST",
              credentials: "same-origin",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ email: email.value, password: password.value })
            });
            if (!login.ok) {
              showStatus(login.status === 401
                ? "Email or password is incorrect. Check the account and try again."
                : "Could not sign in. Please try again.");
              return;
            }
            authenticated = true;
          }
          const acceptance = await fetch("/participant-invitations/accept", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token })
          });
          if (acceptance.ok) {
            showStatus("Invitation accepted. This participant record " +
              "is now linked to your account.");
            form.hidden = true;
          } else if (acceptance.status === 400) {
            showStatus("This invitation is invalid, expired, revoked, or already used. " +
              "Ask the event owner for a new one.");
          } else if (acceptance.status === 401) {
            authenticated = false;
            credentials.hidden = false;
            email.disabled = false;
            password.disabled = false;
            showStatus("Please sign in to accept this invitation.");
          } else {
            showStatus("Could not accept the invitation. Please try again later.");
          }
        } catch (_) {
          showStatus("Network error. Check your connection and try again.");
        } finally {
          button.disabled = false;
        }
      });
    })();
  </script>
</body>
</html>""",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.post("/participant-invitations/accept")
    def accept_participant_invitation(
        payload: AcceptParticipantInvitationRequest,
        request: Request,
        deps: ServiceDependency,
    ) -> dict[str, str]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        accepted = deps.participant_invitations.accept_invitation(
            raw_token=payload.token,
            authenticated_user_id=user_id,
            now=datetime.now(UTC),
        )
        if not accepted:
            raise HTTPException(status_code=400, detail="Invalid or unavailable invitation")
        return {"message": "Invitation accepted"}

    @app.post("/auth/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
    def request_password_reset(
        payload: PasswordResetRequest, deps: ServiceDependency
    ) -> dict[str, str]:
        deps.reset_requests.request(email=payload.email, now=datetime.now(UTC))
        return GENERIC_RESET_RESPONSE

    @app.post("/auth/password-reset/confirm")
    def confirm_password_reset(
        payload: PasswordResetConfirmRequest, deps: ServiceDependency
    ) -> dict[str, str]:
        changed = deps.resets.reset_password(
            raw_token=payload.token,
            new_password=payload.new_password,
            now=datetime.now(UTC),
        )
        if not changed:
            raise HTTPException(status_code=400, detail="Invalid or expired reset token")
        return {"message": "Password updated"}

    return app


app = create_app()

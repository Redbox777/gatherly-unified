"""HTTP adapter for email authentication and server-managed sessions."""

from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4
from typing import cast
import hashlib
import logging
import os
import re
from pathlib import Path
import sqlite3
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
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
from app.domain.expense_splits import SplitInput, SplitMode, calculate_split
from app.domain.money import Money, get_currency
from app.infrastructure.database.auth_email_tokens_repository import (
    AuthEmailTokensRepository,
)
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.connection import connect, migrate, transaction
from app.infrastructure.database.events_repository import EventsRepository
from app.infrastructure.database.event_tasks_repository import EventTasksRepository
from app.infrastructure.database.bug_reports_repository import BugReportsRepository
from app.infrastructure.database.event_expenses_repository import EventExpensesRepository
from app.infrastructure.database.event_expense_splits_repository import (
    EventExpenseSplitsRepository,
)
from app.infrastructure.database.event_finance_repository import EventFinanceRepository
from app.infrastructure.database.event_venues_repository import EventVenuesRepository
from app.infrastructure.database.http_rate_limits_repository import (
    HttpRateLimitsRepository,
)
from app.infrastructure.database.participants_repository import ParticipantsRepository
from app.infrastructure.database.notifications_repository import NotificationsRepository
from app.infrastructure.database.notes_repository import NotesRepository
from app.infrastructure.database.participant_invitations_repository import (
    ParticipantInvitationsRepository,
)
from app.infrastructure.database.smtp_rate_limits_repository import (
    SmtpRateLimitsRepository,
)
from app.infrastructure.email.smtp_email_sender import SmtpEmailSender
from app.infrastructure.observability import (
    bind_request_id,
    configure_application_logging,
    reset_request_id,
    safe_exception_frames,
)
from app.interfaces.telegram.login_verification import verify_telegram_login
from app.interfaces.web.client_ip import (
    IPNetwork,
    load_trusted_proxy_networks,
    resolve_client_ip,
)
from app.interfaces.web.runtime_config import (
    SmtpRateLimitSettings,
    load_smtp_rate_limit_settings,
    validate_runtime_configuration,
)


logger = logging.getLogger(__name__)

SESSION_COOKIE = "gatherly_session"
SESSION_COOKIE_PATH = "/"


def _apply_security_headers(response: Response, *, production: bool) -> Response:
    """Apply baseline browser security headers without overriding route policies."""
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=()")
    if production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


def _token_fragment_link(base_url: str, token: str) -> str:
    """Build a bearer-token link without placing the token in the HTTP request URL."""
    return f"{base_url.split('#', maxsplit=1)[0]}#{urlencode({'token': token})}"


def _is_same_origin(candidate: str, request_url: str, *, origin_header: bool) -> bool:
    """Compare an Origin or Referer value with the request's effective origin."""
    if not candidate or candidate != candidate.strip():
        return False
    try:
        supplied = urlsplit(candidate)
        expected = urlsplit(request_url)
        if (
            supplied.scheme.casefold() not in {"http", "https"}
            or not supplied.hostname
            or supplied.username is not None
            or supplied.password is not None
            or expected.scheme.casefold() not in {"http", "https"}
            or not expected.hostname
        ):
            return False
        if origin_header and (supplied.path or supplied.query or supplied.fragment):
            return False
        supplied_port = supplied.port or (443 if supplied.scheme.casefold() == "https" else 80)
        expected_port = expected.port or (443 if expected.scheme.casefold() == "https" else 80)
    except ValueError:
        return False
    return (
        supplied.scheme.casefold() == expected.scheme.casefold()
        and supplied.hostname.casefold() == expected.hostname.casefold()
        and supplied_port == expected_port
    )


GENERIC_REGISTRATION_RESPONSE = {
    "message": "If registration can be completed, verification instructions will be sent."
}


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


class NotificationPreferencesRequest(BaseModel):
    email_enabled: bool = True
    event_updates_enabled: bool = True
    participant_added_enabled: bool = True
    reminders_enabled: bool = True


class EventReminderRequest(BaseModel):
    offsets_minutes: list[int] = Field(default_factory=list, max_length=3)


class EventVenueRequest(BaseModel):
    venue_name: str = Field(min_length=1, max_length=160)
    address: str = Field(min_length=1, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class EventExpenseRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(pattern="^[A-Z]{3}$")
    paid_by_participant_id: UUID
    incurred_at: datetime | None = None


class ExpenseSplitAllocationRequest(BaseModel):
    participant_id: UUID
    amount: str | None = Field(default=None, min_length=1, max_length=32)
    weight: int | None = Field(default=None, ge=1, le=1000000)


class ExpenseSplitRequest(BaseModel):
    mode: str = Field(pattern="^(equal|fixed|weighted)$")
    allocations: list[ExpenseSplitAllocationRequest] = Field(min_length=1, max_length=200)


class EventSettlementRequest(BaseModel):
    from_participant_id: UUID
    to_participant_id: UUID
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(pattern="^[A-Z]{3}$")
    settled_at: datetime | None = None
    note: str = Field(default="", max_length=1000)


class EventTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    due_at: datetime | None = None
    assignee_participant_id: UUID | None = None


class EventTaskCompletionRequest(BaseModel):
    completed: bool


class CreateNoteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    visibility: str = Field(default="private", pattern="^(private|shared)$")


class UpdateNoteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    visibility: str = Field(pattern="^(private|shared)$")


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


def _serialize_event_expense(expense: dict[str, object]) -> dict[str, object]:
    """Expose an exact decimal string alongside the canonical minor-unit amount."""
    currency = expense["currency"]
    amount_minor_units = expense["amount_minor_units"]
    if not isinstance(currency, str):
        raise TypeError("Expense currency must be a string")
    if isinstance(amount_minor_units, bool) or not isinstance(amount_minor_units, int):
        raise TypeError("Expense minor-unit amount must be an integer")
    money = Money(amount_minor_units, currency)
    precision = get_currency(currency).minor_unit_digits
    return {
        **expense,
        "amount": f"{money.major_units:.{precision}f}",
    }


def _format_finance_amount(amount_minor_units: object, currency: object) -> str:
    if not isinstance(currency, str):
        raise TypeError("Finance currency must be a string")
    if (
        isinstance(amount_minor_units, bool)
        or not isinstance(amount_minor_units, int)
    ):
        raise TypeError("Finance amount must be integer minor units")
    precision = get_currency(currency).minor_unit_digits
    money = Money(amount_minor_units, currency)
    return f"{money.major_units:.{precision}f}"


def _serialize_minor_amount(
    item: dict[str, object], field: str
) -> dict[str, object]:
    amount = _format_finance_amount(item.get(field), item.get("currency"))
    return {
        **item,
        field.replace("_minor_units", ""): amount,
        "amount": amount,
    }


def _serialize_settlement(settlement: dict[str, object]) -> dict[str, object]:
    return _serialize_minor_amount(settlement, "amount_minor_units")


class BugReportRequest(BaseModel):
    consent: bool
    app_version: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._+-]+$")
    browser: str = Field(pattern=r"^(Chrome|Firefox|Safari|Edge|Other)$")
    operating_system: str = Field(pattern=r"^(Windows|macOS|Linux|Android|iOS|Other)$")
    module: str = Field(pattern=r"^(frontend|manual)$")
    error_code: str = Field(pattern=r"^(window_error|unhandled_rejection|user_reported_issue)$")
    stack: str = Field(default="", max_length=1000)


class Dependencies:
    """Construct request-scoped services around a database connection and mail adapter."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        smtp_rate_limit_settings: SmtpRateLimitSettings,
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
        self.email_sender = _email_sender()
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


def _requester_ip(request: Request) -> str:
    """Return the client IP, trusting forwarding headers only from configured proxies."""
    peer_host = request.client.host if request.client else None
    trusted_proxies: tuple[IPNetwork, ...] = request.app.state.trusted_proxy_networks
    return resolve_client_ip(
        peer_host=peer_host,
        forwarded_for=request.headers.get("x-forwarded-for"),
        trusted_proxies=trusted_proxies,
    )


def _consume_http_rate_limit(
    database_path: str | Path, requester: str, route: str
) -> int | None:
    """Perform blocking SQLite throttling work outside the ASGI event loop."""
    connection = connect(database_path)
    try:
        return HttpRateLimitsRepository(connection).consume(
            requester=requester,
            route=route,
            now=datetime.now(UTC),
            limit=5,
            window_seconds=60,
        )
    finally:
        connection.close()


def _enforce_email_send_rate_limit(
    deps: Dependencies, request: Request, recipient: str, now: datetime
) -> None:
    """Apply a shared, database-backed quota before attempting SMTP delivery."""
    requester = _requester_ip(request)
    retry_after = deps.smtp_rate_limits.consume(
        recipient=recipient, requester=requester, now=now
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many email requests; try again later",
            headers={"Retry-After": str(retry_after)},
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


def _send_verification_email(
    deps: Dependencies, *, identity_id: str, recipient: str, now: datetime
) -> bool:
    """Deliver a staged verification token before rotating the active token."""
    token, token_id = deps.verification.issue_pending(identity_id=identity_id, now=now)
    verify_url = os.environ.get(
        "EMAIL_VERIFICATION_URL", "http://localhost:8000/verify-email"
    )
    verification_link = _token_fragment_link(verify_url, token)
    try:
        deps.email_sender.send_text(
            recipient=recipient,
            subject="Verify your Gatherly email",
            body=(
                "Confirm your email address using this one-time token:\n\n"
                f"{verification_link}\n\n"
                "If you did not create this account, you can ignore this email."
            ),
        )
    except Exception as error:
        try:
            deps.verification.discard_pending(token_id=token_id, now=now)
        except Exception as cleanup_error:
            logger.error(
                "Failed to discard pending verification token (%s)",
                type(cleanup_error).__name__,
            )
        logger.warning(
            "Registration verification email delivery failed (%s)", type(error).__name__
        )
        return False

    if not deps.verification.activate_pending(token_id=token_id, now=now):
        logger.error("Verification token activation failed after email delivery")
        return False
    return True


def create_app(database_path: str | Path | None = None) -> FastAPI:
    """Create an API app; tests may inject a temporary SQLite database path."""
    configure_application_logging()
    validate_runtime_configuration(os.environ)
    smtp_rate_limit_settings = load_smtp_rate_limit_settings(os.environ)
    app = FastAPI(title="Gatherly Authentication API", version="0.1.0")
    app.state.trusted_proxy_networks = load_trusted_proxy_networks(
        os.environ.get("TRUSTED_PROXY_CIDRS")
    )
    production = (
        os.environ.get("APP_ENV", "development").strip().casefold() == "production"
    )
    rate_limit_paths = {
        "/auth/login",
        "/auth/telegram/login",
        "/auth/register",
        "/auth/password-reset/request",
        "/auth/password-reset/confirm",
        "/auth/password/change",
        "/auth/verify-email",
        "/auth/verification/request",
        "/participant-invitations/accept",
        "/diagnostics/bug-reports",
    }

    @app.middleware("http")
    async def validate_browser_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        context_token = bind_request_id(request_id)
        started_at = perf_counter()

        def finish(response: Response) -> Response:
            response.headers["X-Request-ID"] = request_id
            if request.url.path in {"/", "/index.html", "/auth.html"}:
                response.headers.setdefault(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'self'; font-src 'self'; "
                    "img-src 'self' data:; "
                    "script-src 'self'; connect-src 'self'; base-uri 'self'; "
                    "form-action 'self'; frame-ancestors 'none'",
                )
            secured = _apply_security_headers(response, production=production)
            logger.info(
                "HTTP request completed",
                extra={
                    "request_id": request_id,
                    "event": "http.request.completed",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": secured.status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                },
            )
            return secured

        try:
            if request.method == "POST" and request.url.path in rate_limit_paths:
                requester = _requester_ip(request)
                retry_after = await run_in_threadpool(
                    _consume_http_rate_limit,
                    request.app.state.database_path,
                    requester,
                    request.url.path,
                )
                if retry_after is not None:
                    return finish(
                        Response(
                            content='{"detail":"Too many requests; try again later"}',
                            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            headers={"Retry-After": str(retry_after)},
                            media_type="application/json",
                        )
                    )

            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                origin = request.headers.get("origin")
                referer = request.headers.get("referer")
                request_url = os.environ.get("PUBLIC_ORIGIN", "").strip() or str(
                    request.base_url
                )
                if origin is not None:
                    # An explicit Origin takes precedence; malformed, opaque ("null"),
                    # and cross-origin values are rejected rather than ignored.
                    same_origin = _is_same_origin(
                        origin, request_url, origin_header=True
                    )
                elif referer is not None:
                    # Some clients omit Origin but send Referer. Validate its origin.
                    same_origin = _is_same_origin(
                        referer, request_url, origin_header=False
                    )
                else:
                    # Cookie-authenticated state changes must prove their browser
                    # origin. Header-less requests remain available to clients that
                    # do not rely on the server-managed browser session cookie.
                    same_origin = SESSION_COOKIE not in request.cookies
                if not same_origin:
                    return finish(
                        Response(
                            content='{"detail":"Cross-origin request rejected"}',
                            status_code=status.HTTP_403_FORBIDDEN,
                            media_type="application/json",
                        )
                    )

            return finish(await call_next(request))
        except Exception as error:
            logger.error(
                "Unhandled HTTP request exception",
                extra={
                    "request_id": request_id,
                    "event": "http.request.exception",
                    "method": request.method,
                    "path": request.url.path,
                    "exception_type": type(error).__name__,
                    "exception_frames": safe_exception_frames(error),
                },
            )
            return finish(
                JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={
                        "detail": "Internal server error",
                        "request_id": request_id,
                    },
                )
            )
        finally:
            reset_request_id(context_token)
    path = database_path or os.environ.get("GATHERLY_DATABASE_PATH", "var/gatherly.sqlite3")
    migration_connection = connect(path)
    try:
        migrate(migration_connection)
    finally:
        migration_connection.close()
    app.state.database_path = path

    def services(request: Request) -> Iterator[Dependencies]:
        # A connection is never shared across HTTP requests. SQLite still
        # serializes writers, while independent request connections prevent
        # one request's commit/rollback from affecting another request.
        connection = connect(request.app.state.database_path)
        try:
            yield Dependencies(connection, smtp_rate_limit_settings)
        finally:
            connection.close()

    ServiceDependency = Annotated[Dependencies, Depends(services)]

    @app.get("/verify-email", response_class=HTMLResponse)
    def verify_email_page() -> HTMLResponse:
        """Serve the same-origin email verification page."""
        return HTMLResponse(
            content="""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Подтверждение почты — Gatherly</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 1rem; }
    main { width: min(100%, 28rem); box-sizing: border-box; padding: 1.5rem;
      border: 1px solid #8886; border-radius: 1rem; }
    button { width: 100%; padding: .75rem; margin-top: 1rem; font: inherit; cursor: pointer; }
    #status { min-height: 1.5em; overflow-wrap: anywhere; }
  </style>
</head>
<body>
  <main>
    <h1>Подтвердите электронную почту</h1>
    <p>Это поможет защитить ваш аккаунт Gatherly.</p>
    <button id="verify" type="button">Подтвердить почту</button>
    <p id="status" role="status" aria-live="polite"></p>
    <p><a href="/auth.html">Перейти ко входу</a></p>
  </main>
  <script>
    (() => {
      let token = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
      window.history.replaceState({}, document.title, window.location.pathname);
      const button = document.getElementById("verify");
      const status = document.getElementById("status");
      button.addEventListener("click", async () => {
        if (!token) {
          status.textContent = "В ссылке нет токена. " +
            "Запросите новое письмо для подтверждения почты.";
          return;
        }
        button.disabled = true;
        try {
          const response = await fetch("/auth/verify-email", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token })
          });
          token = "";
          status.textContent = response.ok
            ? "Почта подтверждена. Теперь можно войти в Gatherly."
            : "Ссылка недействительна, устарела или уже использована. Запросите новое письмо.";
          if (response.ok) button.hidden = true;
        } catch (_) {
          status.textContent = "Ошибка соединения. Проверьте интернет и попробуйте ещё раз.";
        } finally {
          button.disabled = false;
        }
      });
    })();
  </script>
</body>
</html>""",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; connect-src 'self'; "
                    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                ),
            },
        )

    @app.get("/reset-password", response_class=HTMLResponse)
    def reset_password_page() -> HTMLResponse:
        """Serve the same-origin password-reset page."""
        return HTMLResponse(
            content="""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Сброс пароля — Gatherly</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 1rem; }
    main { width: min(100%, 28rem); box-sizing: border-box; padding: 1.5rem;
      border: 1px solid #8886; border-radius: 1rem; }
    label { display: block; margin-top: 1rem; }
    input, button { box-sizing: border-box; width: 100%; padding: .75rem;
      margin-top: .35rem; font: inherit; }
    button { margin-top: 1.25rem; cursor: pointer; }
    #status { min-height: 1.5em; overflow-wrap: anywhere; }
  </style>
</head>
<body>
  <main>
    <h1>Создайте новый пароль</h1>
    <form id="reset-form">
      <label for="password">Новый пароль
        <input id="password" name="password" type="password" autocomplete="new-password"
          minlength="12" maxlength="1024" required>
      </label>
      <button id="submit" type="submit">Сохранить пароль</button>
    </form>
    <p id="status" role="status" aria-live="polite"></p>
    <p><a href="/auth.html">Перейти ко входу</a></p>
  </main>
  <script>
    (() => {
      let token = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
      window.history.replaceState({}, document.title, window.location.pathname);
      const form = document.getElementById("reset-form");
      const password = document.getElementById("password");
      const button = document.getElementById("submit");
      const status = document.getElementById("status");
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!token) {
          status.textContent = "В ссылке нет токена. " +
            "Запросите новое письмо для сброса пароля.";
          return;
        }
        button.disabled = true;
        try {
          const response = await fetch("/auth/password-reset/confirm", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token, new_password: password.value })
          });
          token = "";
          if (response.ok) {
            password.value = "";
            form.hidden = true;
            status.textContent = "Пароль обновлён. Теперь можно войти в Gatherly.";
          } else {
            status.textContent = "Ссылка недействительна, устарела или уже использована. " +
              "Запросите новое письмо для сброса пароля.";
          }
        } catch (_) {
          status.textContent = "Ошибка соединения. Проверьте интернет и попробуйте ещё раз.";
        } finally {
          button.disabled = false;
        }
      });
    })();
  </script>
</body>
</html>""",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; connect-src 'self'; "
                    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                ),
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/register", status_code=status.HTTP_202_ACCEPTED)
    def register(
        payload: RegisterRequest, request: Request, deps: ServiceDependency
    ) -> dict[str, str]:
        now = datetime.now(UTC)
        _enforce_email_send_rate_limit(deps, request, payload.email, now)
        try:
            identity = deps.identities.register_email(
                email=payload.email, password=payload.password, now=now
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid registration data") from error
        except sqlite3.IntegrityError:
            # Do not reveal whether this email already belongs to an account.
            return GENERIC_REGISTRATION_RESPONSE
        _send_verification_email(
            deps,
            identity_id=identity.identity_id,
            recipient=identity.provider_subject,
            now=now,
        )
        # Delivery failures use the same public response; the user can request
        # another verification message without learning whether an account exists.
        return GENERIC_REGISTRATION_RESPONSE

    @app.post("/auth/verification/request", status_code=status.HTTP_202_ACCEPTED)
    def request_verification(
        payload: PasswordResetRequest, request: Request, deps: ServiceDependency
    ) -> dict[str, str]:
        normalized_email = payload.email.strip().casefold()
        now = datetime.now(UTC)
        retry_after = deps.smtp_rate_limits.consume(
            recipient=normalized_email,
            requester=_requester_ip(request),
            now=now,
        )
        identity = (
            None
            if retry_after is not None
            else deps.identities.find_identity(
                provider="email", provider_subject=normalized_email
            )
        )
        if identity is not None and identity.verified_at is None:
            _send_verification_email(
                deps,
                identity_id=identity.identity_id,
                recipient=identity.provider_subject,
                now=now,
            )
        return {
            "message": (
                "If an unverified account exists for that email address, "
                "verification instructions will be sent."
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
            with transaction(deps.connection):
                deps.connection.execute(
                    "DELETE FROM auth_telegram_login_payloads WHERE consumed_at < ?",
                    (datetime.fromtimestamp(now.timestamp() - 600, tz=UTC).isoformat(),),
                )
                deps.connection.execute(
                    "INSERT INTO auth_telegram_login_payloads(payload_hash, consumed_at) "
                    "VALUES (?, ?)",
                    (signature_hash, now.isoformat()),
                )
        except sqlite3.IntegrityError:
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

    @app.get("/notifications")
    def list_notifications(request: Request, deps: ServiceDependency) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return {
            "items": deps.notifications.list_for_user(user_id=user_id),
            "unread_count": deps.notifications.unread_count(user_id=user_id),
        }

    @app.post("/notifications/{notification_id}/read")
    def mark_notification_read(
        notification_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, bool]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        changed = deps.notifications.mark_read(
            user_id=user_id,
            notification_id=str(notification_id),
            now=datetime.now(UTC),
        )
        if not changed:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"read": True}

    @app.get("/notification-preferences")
    def get_notification_preferences(
        request: Request, deps: ServiceDependency
    ) -> dict[str, bool]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return deps.notifications.get_preferences(user_id=user_id)

    @app.put("/notification-preferences")
    def update_notification_preferences(
        payload: NotificationPreferencesRequest,
        request: Request,
        deps: ServiceDependency,
    ) -> dict[str, bool]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return deps.notifications.set_preferences(
            user_id=user_id,
            values=payload.model_dump(),
            now=datetime.now(UTC),
        )

    @app.get("/events/{event_id}/reminders")
    def get_event_reminders(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, list[int]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            deps.events.get_event(event_id=event_id, owner_id=owner_id)
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        return {
            "offsets_minutes": deps.notifications.get_event_reminders(
                event_id=str(event_id), owner_id=owner_id
            )
        }

    @app.put("/events/{event_id}/reminders")
    def update_event_reminders(
        event_id: UUID,
        payload: EventReminderRequest,
        request: Request,
        deps: ServiceDependency,
    ) -> dict[str, list[int]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            event = deps.events.get_event(event_id=event_id, owner_id=owner_id)
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        if payload.offsets_minutes and event.starts_at is None:
            raise HTTPException(
                status_code=422,
                detail="Set the event date and time before enabling reminders",
            )
        try:
            deps.notifications.set_event_reminders(
                event_id=str(event_id),
                owner_id=owner_id,
                offsets=payload.offsets_minutes,
                now=datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Unsupported reminder offset") from error
        return {
            "offsets_minutes": deps.notifications.get_event_reminders(
                event_id=str(event_id), owner_id=owner_id
            )
        }

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
        deps.notifications.clear_pending_event_reminders(event_id=str(event_id))
        notification_time = datetime.now(UTC)
        linked_participants = deps.participants.list_participants(
            event_id=event_id, owner_id=user_id
        )
        for participant in linked_participants:
            recipient_id = (
                str(participant.linked_user_id) if participant.linked_user_id else None
            )
            if recipient_id is None or recipient_id == user_id:
                continue
            preferences = deps.notifications.get_preferences(user_id=recipient_id)
            if preferences["event_updates_enabled"]:
                deps.notifications.create(
                    user_id=recipient_id,
                    event_id=str(event_id),
                    kind="event_updated",
                    title="Встреча обновлена",
                    body=f"Встреча «{event.title}» была изменена.",
                    dedupe_key=f"event-updated:{event_id}:{recipient_id}:{notification_time.isoformat()}",
                    now=notification_time,
                )
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
        event = deps.events.get_event(event_id=event_id, owner_id=user_id)
        notification_time = datetime.now(UTC)
        for existing_participant in deps.participants.list_participants(
            event_id=event_id, owner_id=user_id
        ):
            recipient_id = (
                str(existing_participant.linked_user_id)
                if existing_participant.linked_user_id else None
            )
            if recipient_id is None or recipient_id == user_id:
                continue
            preferences = deps.notifications.get_preferences(user_id=recipient_id)
            if preferences["participant_added_enabled"]:
                deps.notifications.create(
                    user_id=recipient_id,
                    event_id=str(event_id),
                    kind="participant_added",
                    title="Новый участник встречи",
                    body=(
                        f"К встрече «{event.title}» добавлен участник: "
                        f"{participant.display_name}."
                    ),
                    dedupe_key=(
                        f"participant-added:{participant.id}:{recipient_id}"
                    ),
                    now=notification_time,
                )
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
        _enforce_email_send_rate_limit(deps, request, payload.email, now)
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
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; connect-src 'self'; "
                    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                ),
            },
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
        payload: PasswordResetRequest, request: Request, deps: ServiceDependency
    ) -> dict[str, str]:
        normalized_email = payload.email.strip().casefold()
        now = datetime.now(UTC)
        retry_after = deps.smtp_rate_limits.consume(
            recipient=normalized_email,
            requester=_requester_ip(request),
            now=now,
        )
        if retry_after is None:
            deps.reset_requests.request(email=normalized_email, now=now)
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


    @app.get("/events/{event_id}/venue")
    def get_event_venue(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, object] | None:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            deps.events.get_event(event_id=event_id, owner_id=owner_id)
        except EventNotFoundError as error:
            raise HTTPException(status_code=404, detail="Event not found") from error
        venue = deps.event_venues.get_for_owner(event_id=str(event_id), owner_id=owner_id)
        return venue

    @app.put("/events/{event_id}/venue")
    def save_event_venue(
        event_id: UUID, payload: EventVenueRequest, request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            venue = deps.event_venues.save_for_owner(
                event_id=str(event_id), owner_id=owner_id,
                venue_name=payload.venue_name, address=payload.address,
                latitude=payload.latitude, longitude=payload.longitude,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid venue data") from error
        if venue is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return venue

    @app.delete("/events/{event_id}/venue", status_code=status.HTTP_204_NO_CONTENT)
    def delete_event_venue(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> Response:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not deps.event_venues.delete_for_owner(event_id=str(event_id), owner_id=owner_id):
            raise HTTPException(status_code=404, detail="Venue not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/events/{event_id}/tasks")
    def list_event_tasks(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> list[dict[str, object]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        tasks = deps.event_tasks.list_for_owner(
            event_id=str(event_id), owner_id=owner_id
        )
        if tasks is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return tasks

    @app.post(
        "/events/{event_id}/tasks", status_code=status.HTTP_201_CREATED
    )
    def create_event_task(
        event_id: UUID, payload: EventTaskRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if payload.due_at is not None and (
            payload.due_at.tzinfo is None or payload.due_at.utcoffset() is None
        ):
            raise HTTPException(status_code=422, detail="Task due date must include a timezone")
        try:
            task = deps.event_tasks.create(
                event_id=str(event_id), owner_id=owner_id,
                title=payload.title, description=payload.description,
                due_at=payload.due_at.isoformat() if payload.due_at else None,
                assignee_participant_id=(
                    str(payload.assignee_participant_id)
                    if payload.assignee_participant_id else None
                ),
                now=datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid task data") from error
        if task is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return task

    @app.put("/events/{event_id}/tasks/{task_id}")
    def update_event_task(
        event_id: UUID, task_id: UUID, payload: EventTaskRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if payload.due_at is not None and (
            payload.due_at.tzinfo is None or payload.due_at.utcoffset() is None
        ):
            raise HTTPException(status_code=422, detail="Task due date must include a timezone")
        existing = deps.event_tasks.get_for_owner(
            task_id=str(task_id), event_id=str(event_id), owner_id=owner_id
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="Task not found")
        try:
            task = deps.event_tasks.update(
                task_id=str(task_id), event_id=str(event_id), owner_id=owner_id,
                title=payload.title, description=payload.description,
                due_at=payload.due_at.isoformat() if payload.due_at else None,
                assignee_participant_id=(
                    str(payload.assignee_participant_id)
                    if payload.assignee_participant_id else None
                ),
                completed=bool(existing["completed"]),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid task data") from error
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @app.patch("/events/{event_id}/tasks/{task_id}/completion")
    def set_event_task_completion(
        event_id: UUID, task_id: UUID, payload: EventTaskCompletionRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        task = deps.event_tasks.set_completed(
            task_id=str(task_id), event_id=str(event_id),
            owner_id=owner_id, completed=payload.completed,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    @app.delete(
        "/events/{event_id}/tasks/{task_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_event_task(
        event_id: UUID, task_id: UUID, request: Request, deps: ServiceDependency
    ) -> Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        owner_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not deps.event_tasks.delete(
            task_id=str(task_id), event_id=str(event_id), owner_id=owner_id
        ):
            raise HTTPException(status_code=404, detail="Task not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/events/{event_id}/expenses")
    def list_event_expenses(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> list[dict[str, object]]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        expenses = deps.event_expenses.list_for_owner(
            event_id=str(event_id), owner_id=owner_id
        )
        if expenses is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return [_serialize_event_expense(expense) for expense in expenses]

    @app.post(
        "/events/{event_id}/expenses", status_code=status.HTTP_201_CREATED
    )
    def create_event_expense(
        event_id: UUID, payload: EventExpenseRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        now = datetime.now(UTC)
        incurred_at = payload.incurred_at or now
        if incurred_at.tzinfo is None or incurred_at.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Expense date must include a timezone")
        try:
            money = Money.from_major(payload.amount, payload.currency)
            expense = deps.event_expenses.create(
                event_id=str(event_id), owner_id=owner_id,
                title=payload.title, description=payload.description,
                amount_minor_units=money.minor_units, currency=money.currency,
                paid_by_participant_id=str(payload.paid_by_participant_id),
                incurred_at=incurred_at, now=now,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid expense data") from error
        if expense is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return _serialize_event_expense(expense)

    @app.put("/events/{event_id}/expenses/{expense_id}")
    def update_event_expense(
        event_id: UUID, expense_id: UUID, payload: EventExpenseRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        now = datetime.now(UTC)
        incurred_at = payload.incurred_at or now
        if incurred_at.tzinfo is None or incurred_at.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Expense date must include a timezone")
        try:
            money = Money.from_major(payload.amount, payload.currency)
            expense = deps.event_expenses.update(
                expense_id=str(expense_id), event_id=str(event_id), owner_id=owner_id,
                title=payload.title, description=payload.description,
                amount_minor_units=money.minor_units, currency=money.currency,
                paid_by_participant_id=str(payload.paid_by_participant_id),
                incurred_at=incurred_at, now=now,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid expense data") from error
        if expense is None:
            raise HTTPException(status_code=404, detail="Expense not found")
        return _serialize_event_expense(expense)

    @app.get("/events/{event_id}/expenses/{expense_id}/split")
    def get_event_expense_split(
        event_id: UUID, expense_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        split = deps.event_expense_splits.get_for_owner(
            event_id=str(event_id), expense_id=str(expense_id), owner_id=owner_id
        )
        if split is None:
            raise HTTPException(status_code=404, detail="Expense not found")
        currency = split["currency"]
        if not isinstance(currency, str):
            raise TypeError("Expense currency must be a string")
        precision = get_currency(currency).minor_unit_digits
        allocations = []
        for allocation in cast(list[dict[str, object]], split["allocations"]):
            minor_units = allocation["amount_minor_units"]
            if isinstance(minor_units, bool) or not isinstance(minor_units, int):
                raise TypeError("Allocation amount must be integer minor units")
            money = Money(minor_units, currency)
            allocations.append({
                **allocation,
                "amount": f"{money.major_units:.{precision}f}",
            })
        return {**split, "allocations": allocations}

    @app.put("/events/{event_id}/expenses/{expense_id}/split")
    def replace_event_expense_split(
        event_id: UUID, expense_id: UUID, payload: ExpenseSplitRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            mode = SplitMode(payload.mode)
            split = deps.event_expense_splits.get_for_owner(
                event_id=str(event_id), expense_id=str(expense_id), owner_id=owner_id
            )
            if split is None:
                raise HTTPException(status_code=404, detail="Expense not found")
            currency = split["currency"]
            if not isinstance(currency, str):
                raise TypeError("Expense currency must be a string")
            inputs = []
            for allocation in payload.allocations:
                amount_minor_units = None
                if allocation.amount is not None:
                    amount_minor_units = Money.from_major(
                        allocation.amount, currency
                    ).minor_units
                inputs.append(SplitInput(
                    participant_id=str(allocation.participant_id),
                    amount_minor_units=amount_minor_units,
                    weight=allocation.weight,
                ))
            total_minor_units = split["amount_minor_units"]
            if isinstance(total_minor_units, bool) or not isinstance(total_minor_units, int):
                raise TypeError("Expense amount must be integer minor units")
            shares = calculate_split(
                Money(total_minor_units, currency), mode, inputs
            )
            saved = deps.event_expense_splits.replace_for_owner(
                event_id=str(event_id), expense_id=str(expense_id),
                owner_id=owner_id, mode=mode, shares=shares,
            )
        except HTTPException:
            raise
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid expense split") from error
        if saved is None:
            raise HTTPException(status_code=404, detail="Expense not found")
        precision = get_currency(currency).minor_unit_digits
        result_allocations = []
        for saved_allocation in cast(
            list[dict[str, object]], saved["allocations"]
        ):
            saved_minor_units = saved_allocation["amount_minor_units"]
            if isinstance(saved_minor_units, bool) or not isinstance(saved_minor_units, int):
                raise TypeError("Allocation amount must be integer minor units")
            money = Money(saved_minor_units, currency)
            result_allocations.append({
                **saved_allocation,
                "amount": f"{money.major_units:.{precision}f}",
            })
        return {**saved, "allocations": result_allocations}

    @app.get("/events/{event_id}/balances")
    def get_event_balances(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        result = deps.event_finance.get_balances_for_owner(
            event_id=str(event_id), owner_id=owner_id
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Event not found")
        balances = cast(list[dict[str, object]], result["balances"])
        pending = cast(list[dict[str, object]], result["pending_expenses"])
        unresolved = cast(list[dict[str, object]], result["unresolved"])
        return {
            **result,
            "balances": [
                _serialize_minor_amount(item, "balance_minor_units")
                for item in balances
            ],
            "pending_expenses": [
                _serialize_minor_amount(item, "amount_minor_units")
                for item in pending
            ],
            "unresolved": [
                {
                    **item,
                    "missing_payer": _format_finance_amount(
                        item["missing_payer_minor_units"], item["currency"]
                    ),
                    "removed_participant_allocations": _format_finance_amount(
                        item["removed_participant_allocations_minor_units"],
                        item["currency"],
                    ),
                    "settlements_with_removed_participant": _format_finance_amount(
                        item["settlements_with_removed_participant_minor_units"],
                        item["currency"],
                    ),
                }
                for item in unresolved
            ],
        }

    @app.get("/events/{event_id}/settlements")
    def list_event_settlements(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> list[dict[str, object]]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        settlements = deps.event_finance.list_settlements_for_owner(
            event_id=str(event_id), owner_id=owner_id
        )
        if settlements is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return [_serialize_settlement(item) for item in settlements]

    @app.post("/events/{event_id}/settlements", status_code=status.HTTP_201_CREATED)
    def create_event_settlement(
        event_id: UUID, payload: EventSettlementRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        now = datetime.now(UTC)
        settled_at = payload.settled_at or now
        if settled_at.tzinfo is None or settled_at.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Settlement date must include a timezone")
        try:
            money = Money.from_major(payload.amount, payload.currency)
            settlement = deps.event_finance.create_settlement_for_owner(
                event_id=str(event_id), owner_id=owner_id,
                from_participant_id=str(payload.from_participant_id),
                to_participant_id=str(payload.to_participant_id),
                amount_minor_units=money.minor_units, currency=money.currency,
                settled_at=settled_at, note=payload.note, now=now,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid settlement") from error
        if settlement is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return _serialize_settlement(settlement)

    @app.delete(
        "/events/{event_id}/expenses/{expense_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_event_expense(
        event_id: UUID, expense_id: UUID, request: Request, deps: ServiceDependency
    ) -> Response:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not deps.event_expenses.delete(
            expense_id=str(expense_id), event_id=str(event_id), owner_id=owner_id
        ):
            raise HTTPException(status_code=404, detail="Expense not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/events/{event_id}/notes")
    def list_event_notes(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> list[dict[str, object]]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        notes = deps.notes.list_for_event(event_id=str(event_id), user_id=user_id)
        if notes is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return notes

    @app.post("/events/{event_id}/notes", status_code=status.HTTP_201_CREATED)
    def create_event_note(
        event_id: UUID, payload: CreateNoteRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            note = deps.notes.create(
                event_id=str(event_id), author_id=user_id,
                content=payload.content, visibility=payload.visibility,
                now=datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid note data") from error
        if note is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return note

    @app.put("/events/{event_id}/notes/{note_id}")
    def update_event_note(
        event_id: UUID, note_id: UUID, payload: UpdateNoteRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            note = deps.notes.update(
                event_id=str(event_id), note_id=str(note_id), user_id=user_id,
                content=payload.content, visibility=payload.visibility,
                now=datetime.now(UTC),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid note data") from error
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found")
        return note

    @app.delete(
        "/events/{event_id}/notes/{note_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_event_note(
        event_id: UUID, note_id: UUID, request: Request, deps: ServiceDependency
    ) -> Response:
        token = request.cookies.get(SESSION_COOKIE, "")
        user_id = deps.authentication.resolve_session(
            raw_token=token, now=datetime.now(UTC)
        )
        if user_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not deps.notes.delete(
            event_id=str(event_id), note_id=str(note_id), user_id=user_id
        ):
            raise HTTPException(status_code=404, detail="Note not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)


    @app.post("/diagnostics/bug-reports", status_code=status.HTTP_201_CREATED)
    def create_bug_report(payload: BugReportRequest, request: Request) -> dict[str, str]:
        if not payload.consent:
            raise HTTPException(status_code=422, detail="Explicit consent is required")
        safe_frames = [
            line.strip()
            for line in payload.stack.splitlines()
            if re.fullmatch(r"at line [0-9]{1,7}, column [0-9]{1,7}", line.strip())
        ][:8]
        connection = connect(request.app.state.database_path)
        try:
            report_id = BugReportsRepository(connection).create(
                created_at=datetime.now(UTC),
                app_version=payload.app_version,
                browser=payload.browser,
                operating_system=payload.operating_system,
                module=payload.module,
                error_code=payload.error_code,
                stack="\\n".join(safe_frames),
            )
        finally:
            connection.close()
        return {"report_id": report_id, "status": "received"}

    static_directory = Path(__file__).resolve().parent / "static"
    app.mount(
        "/",
        StaticFiles(directory=static_directory, html=True),
        name="frontend",
    )
    return app


app = create_app()

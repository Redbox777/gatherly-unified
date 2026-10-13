"""HTTP adapter for email authentication and server-managed sessions."""

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.auth_sessions_service import AuthSessionsService
from app.application.authentication_service import AuthenticationService
from app.application.email_verification_service import EmailVerificationService
from app.application.password_reset_request_service import PasswordResetRequestService
from app.application.password_reset_service import PasswordResetService
from app.infrastructure.database.auth_email_tokens_repository import (
    AuthEmailTokensRepository,
)
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.email.smtp_email_sender import SmtpEmailSender


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
        return {"message": "If registration can be completed, verification instructions will be sent."}

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

from datetime import UTC, datetime

import pytest

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.password_reset_request_service import (
    GENERIC_RESPONSE,
    PasswordResetRequestService,
)
from app.application.password_reset_service import PasswordResetService
from app.infrastructure.database.auth_email_tokens_repository import (
    AuthEmailTokensRepository,
)
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.connection import connect, migrate


NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
EMAIL = "person@example.test"
PASSWORD = "a secure example password"


class FakeEmailSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self.fail = fail

    def send_text(self, *, recipient: str, subject: str, body: str) -> None:
        if self.fail:
            raise RuntimeError("SMTP unavailable")
        self.messages.append((recipient, subject, body))


@pytest.fixture
def request_context(tmp_path):
    connection = connect(tmp_path / "password-reset-request.sqlite3")
    migrate(connection)
    identity_repository = AuthIdentitiesRepository(connection)
    identities = AuthIdentitiesService(identity_repository)
    identity = identities.register_email(email=EMAIL, password=PASSWORD, now=NOW)
    connection.execute(
        "UPDATE auth_identities SET verified_at = ? WHERE id = ?",
        (NOW.isoformat(), identity.identity_id),
    )
    connection.commit()
    resets = PasswordResetService(AuthEmailTokensRepository(connection))
    sender = FakeEmailSender()
    service = PasswordResetRequestService(
        identities=identities,
        resets=resets,
        email_sender=sender,
        reset_url="https://gatherly.example/reset-password",
    )
    yield connection, identities, identity, sender, service
    connection.close()


def test_known_and_unknown_email_receive_same_public_response(request_context) -> None:
    _, _, _, sender, service = request_context

    known_response = service.request(email=EMAIL, now=NOW)
    unknown_response = service.request(email="missing@example.test", now=NOW)

    assert known_response == GENERIC_RESPONSE
    assert unknown_response == GENERIC_RESPONSE
    assert len(sender.messages) == 1
    recipient, subject, body = sender.messages[0]
    assert recipient == EMAIL
    assert subject == "Reset your Gatherly password"
    assert "https://gatherly.example/reset-password?token=" in body
    assert "If you did not request this" in body


def test_unverified_account_does_not_receive_reset_email(request_context) -> None:
    connection, _, identity, sender, service = request_context
    connection.execute(
        "UPDATE auth_identities SET verified_at = NULL WHERE id = ?",
        (identity.identity_id,),
    )
    connection.commit()

    assert service.request(email=EMAIL, now=NOW) == GENERIC_RESPONSE
    assert sender.messages == []


def test_mail_failure_keeps_generic_response(request_context) -> None:
    _, _, _, sender, service = request_context
    sender.fail = True

    assert service.request(email=EMAIL, now=NOW) == GENERIC_RESPONSE


def test_request_requires_timezone_aware_datetime(request_context) -> None:
    _, _, _, _, service = request_context
    with pytest.raises(ValueError, match="timezone"):
        service.request(email=EMAIL, now=datetime(2026, 10, 13, 12, 0))

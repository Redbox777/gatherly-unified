from datetime import UTC, datetime, timedelta

import pytest

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.email_verification_service import EmailVerificationService
from app.infrastructure.database.auth_email_tokens_repository import (
    AuthEmailTokensRepository,
)
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.connection import connect, migrate


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def services(tmp_path):
    connection = connect(tmp_path / "email-verification.sqlite3")
    migrate(connection)
    identities = AuthIdentitiesRepository(connection)
    tokens = AuthEmailTokensRepository(connection)
    identity_service = AuthIdentitiesService(identities)
    verification_service = EmailVerificationService(tokens, lifetime=timedelta(hours=1))
    yield connection, identity_service, verification_service
    connection.close()


def test_verification_token_marks_email_verified_and_is_one_time(services) -> None:
    connection, identity_service, verification_service = services
    identity = identity_service.register_email(
        email="person@example.test",
        password="a secure example password",
        now=NOW,
    )

    raw_token = verification_service.issue(user_id=identity.user_id, now=NOW)

    stored = connection.execute(
        "SELECT token_hash FROM auth_email_tokens"
    ).fetchone()
    assert stored is not None
    assert stored["token_hash"] != raw_token

    assert verification_service.verify(raw_token=raw_token, now=NOW + timedelta(minutes=1))
    verified = identity_service.find_identity(
        provider="email",
        provider_subject="person@example.test",
    )
    assert verified is not None
    assert verified.verified_at == NOW + timedelta(minutes=1)
    assert identity_service.authenticate_email(
        email="person@example.test", password="a secure example password"
    ) == verified
    with pytest.raises(ValueError, match="unverified email identity"):
        verification_service.issue(user_id=identity.user_id, now=NOW + timedelta(minutes=2))
    assert not verification_service.verify(
        raw_token=raw_token, now=NOW + timedelta(minutes=2)
    )


def test_verification_rejects_unknown_and_expired_tokens(services) -> None:
    _, identity_service, verification_service = services
    identity = identity_service.register_email(
        email="person@example.test",
        password="a secure example password",
        now=NOW,
    )
    raw_token = verification_service.issue(user_id=identity.user_id, now=NOW)

    assert not verification_service.verify(raw_token="unknown-token", now=NOW)
    assert not verification_service.verify(
        raw_token=raw_token, now=NOW + timedelta(hours=1)
    )


def test_verification_service_requires_positive_lifetime(services) -> None:
    connection, _, _ = services
    with pytest.raises(ValueError, match="positive"):
        EmailVerificationService(
            AuthEmailTokensRepository(connection),
            lifetime=timedelta(0),
        )


def test_verification_rejects_naive_datetime(services) -> None:
    _, _, verification_service = services
    with pytest.raises(ValueError, match="timezone"):
        verification_service.issue(
            user_id="user-1",
            now=datetime(2026, 10, 12, 12, 0),
        )

from concurrent.futures import ThreadPoolExecutor
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

    raw_token = verification_service.issue(identity_id=identity.identity_id, now=NOW)

    stored = connection.execute(
        "SELECT token_hash FROM auth_email_tokens"
    ).fetchone()
    assert stored is not None
    assert stored["token_hash"] != raw_token

    assert verification_service.verify(
        raw_token=raw_token, now=NOW + timedelta(minutes=1)
    )
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
        verification_service.issue(
            identity_id=identity.identity_id, now=NOW + timedelta(minutes=2)
        )
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
    raw_token = verification_service.issue(identity_id=identity.identity_id, now=NOW)

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
            identity_id="missing-identity",
            now=datetime(2026, 10, 12, 12, 0),
        )


def test_pending_verification_token_requires_activation_and_can_be_discarded(
    services,
) -> None:
    connection, identity_service, verification_service = services
    identity = identity_service.register_email(
        email="pending@example.test",
        password="a secure example password",
        now=NOW,
    )

    raw_token, token_id = verification_service.issue_pending(
        identity_id=identity.identity_id, now=NOW
    )
    assert not verification_service.verify(raw_token=raw_token, now=NOW)
    assert verification_service.discard_pending(token_id=token_id, now=NOW)
    assert not verification_service.activate_pending(token_id=token_id, now=NOW)
    assert not verification_service.verify(raw_token=raw_token, now=NOW)

    next_raw, next_id = verification_service.issue_pending(
        identity_id=identity.identity_id, now=NOW + timedelta(minutes=1)
    )
    assert verification_service.activate_pending(
        token_id=next_id, now=NOW + timedelta(minutes=1)
    )
    assert verification_service.verify(
        raw_token=next_raw, now=NOW + timedelta(minutes=2)
    )



def test_verification_token_only_verifies_its_bound_identity(services) -> None:
    connection, identity_service, verification_service = services
    identity = identity_service.register_email(
        email="primary@example.test",
        password="a secure example password",
        now=NOW,
    )
    connection.execute(
        """INSERT INTO auth_identities(
            id, user_id, provider, provider_subject, password_hash, verified_at, created_at
        ) VALUES (?, ?, 'email', ?, ?, NULL, ?)""",
        (
            "secondary-identity",
            identity.user_id,
            "secondary@example.test",
            "another-password-hash",
            NOW.isoformat(),
        ),
    )
    connection.commit()

    raw_token = verification_service.issue(identity_id=identity.identity_id, now=NOW)

    assert verification_service.verify(raw_token=raw_token, now=NOW + timedelta(minutes=1))
    primary = connection.execute(
        "SELECT verified_at FROM auth_identities WHERE id = ?",
        (identity.identity_id,),
    ).fetchone()
    secondary = connection.execute(
        "SELECT verified_at FROM auth_identities WHERE id = ?",
        ("secondary-identity",),
    ).fetchone()
    assert primary["verified_at"] == "2026-10-12T12:01:00.000000Z"
    assert secondary["verified_at"] is None


def test_concurrent_verification_token_consumption_succeeds_exactly_once(tmp_path) -> None:
    database_path = tmp_path / "concurrent-verification.sqlite3"
    setup = connect(database_path)
    migrate(setup)
    identity = AuthIdentitiesService(AuthIdentitiesRepository(setup)).register_email(
        email="concurrent@example.test",
        password="a secure example password",
        now=NOW,
    )
    raw_token = EmailVerificationService(
        AuthEmailTokensRepository(setup)
    ).issue(identity_id=identity.identity_id, now=NOW)
    setup.close()

    def consume() -> bool:
        connection = connect(database_path)
        try:
            service = EmailVerificationService(AuthEmailTokensRepository(connection))
            return service.verify(raw_token=raw_token, now=NOW + timedelta(minutes=1))
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))

    assert sorted(results) == [False, True]

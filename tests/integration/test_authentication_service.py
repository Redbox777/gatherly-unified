from datetime import UTC, datetime, timedelta

import pytest

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.auth_sessions_service import AuthSessionsService
from app.application.authentication_service import AuthenticationService
from app.domain.passwords import hash_password
from app.infrastructure.database.auth_identities_repository import (
    AuthIdentitiesRepository,
    AuthIdentity,
)
from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.connection import connect, migrate


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def authentication_service(tmp_path):
    connection = connect(tmp_path / "authentication-service.sqlite3")
    migrate(connection)
    service = AuthenticationService(
        AuthIdentitiesService(AuthIdentitiesRepository(connection)),
        AuthSessionsService(
            AuthSessionsRepository(connection),
            lifetime=timedelta(hours=2),
        ),
    )
    yield service, connection
    connection.close()


def test_email_login_issues_session_and_logout_revokes_it(authentication_service) -> None:
    service, connection = authentication_service
    identity = AuthIdentitiesService(AuthIdentitiesRepository(connection)).register_email(
        email="person@example.test",
        password="a secure example password",
        now=NOW,
    )
    connection.execute(
        "UPDATE auth_identities SET verified_at = ? WHERE id = ?",
        (NOW.isoformat(), identity.identity_id),
    )
    connection.commit()

    session = service.login_email(
        email="PERSON@example.test",
        password="a secure example password",
        now=NOW,
    )

    assert session is not None
    assert session.user_id == identity.user_id
    assert session.expires_at == NOW + timedelta(hours=2)
    assert service.resolve_session(raw_token=session.raw_token, now=NOW) == identity.user_id
    assert service.logout(raw_token=session.raw_token, now=NOW + timedelta(minutes=1))
    assert service.resolve_session(raw_token=session.raw_token, now=NOW) is None


def test_failed_or_unverified_login_does_not_issue_session(authentication_service) -> None:
    service, connection = authentication_service
    identities = AuthIdentitiesService(AuthIdentitiesRepository(connection))
    identities.register_email(
        email="person@example.test",
        password="a secure example password",
        now=NOW,
    )

    assert service.login_email(
        email="person@example.test", password="wrong password", now=NOW
    ) is None
    assert service.login_email(
        email="person@example.test",
        password="a secure example password",
        now=NOW,
    ) is None
    count = connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
    assert count == 0


def test_login_does_not_issue_session_if_password_changes_after_verification(
    tmp_path, monkeypatch
) -> None:
    connection = connect(tmp_path / "authentication-password-race.sqlite3")
    migrate(connection)
    identity_repository = AuthIdentitiesRepository(connection)
    identities = AuthIdentitiesService(identity_repository)
    sessions = AuthSessionsService(
        AuthSessionsRepository(connection),
        lifetime=timedelta(hours=2),
    )
    service = AuthenticationService(identities, sessions)
    identity = identities.register_email(
        email="race@example.test",
        password="the original secure password",
        now=NOW,
    )
    connection.execute(
        "UPDATE auth_identities SET verified_at = ? WHERE id = ?",
        (NOW.isoformat(), identity.identity_id),
    )
    connection.commit()

    original_authenticate = identities.authenticate_email

    def authenticate_then_change_password(
        *, email: str, password: str
    ) -> AuthIdentity | None:
        authenticated = original_authenticate(email=email, password=password)
        assert authenticated is not None
        assert identity_repository.update_password_and_revoke_sessions(
            user_id=identity.user_id,
            current_password_hash=authenticated.password_hash or "",
            new_password_hash=hash_password("the replacement secure password"),
            now=NOW + timedelta(seconds=1),
        )
        return authenticated

    monkeypatch.setattr(
        identities, "authenticate_email", authenticate_then_change_password
    )
    try:
        session = service.login_email(
            email="race@example.test",
            password="the original secure password",
            now=NOW + timedelta(seconds=2),
        )

        assert session is None
        count = connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
        assert count == 0
    finally:
        connection.close()

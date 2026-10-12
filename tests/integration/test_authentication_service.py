from datetime import UTC, datetime, timedelta

import pytest

from app.application.auth_identities_service import AuthIdentitiesService
from app.application.auth_sessions_service import AuthSessionsService
from app.application.authentication_service import AuthenticationService
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
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

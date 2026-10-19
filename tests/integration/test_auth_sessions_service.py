from datetime import UTC, datetime, timedelta

import pytest

from app.application.auth_sessions_service import AuthSessionsService
from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.connection import connect, migrate


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def service(tmp_path):
    connection = connect(tmp_path / "auth-service.sqlite3")
    migrate(connection)
    connection.execute(
        "INSERT INTO users(id, created_at) VALUES (?, ?)",
        ("user-1", NOW.isoformat()),
    )
    connection.commit()
    instance = AuthSessionsService(
        AuthSessionsRepository(connection),
        lifetime=timedelta(hours=2),
    )
    yield instance
    connection.close()


def test_service_issues_resolves_and_revokes_session(service) -> None:
    session = service.create(user_id="user-1", now=NOW)

    assert session.expires_at == NOW + timedelta(hours=2)
    assert service.resolve(raw_token=session.raw_token, now=NOW) == "user-1"
    assert service.revoke(
        raw_token=session.raw_token, now=NOW + timedelta(minutes=1)
    )
    assert service.resolve(raw_token=session.raw_token, now=NOW) is None


def test_service_rejects_non_positive_lifetime() -> None:
    with pytest.raises(ValueError, match="positive"):
        AuthSessionsService(
            AuthSessionsRepository(connect(":memory:")),
            lifetime=timedelta(0),
        )

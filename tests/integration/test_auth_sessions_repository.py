from datetime import datetime, timedelta, timezone

import pytest

from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.connection import connect, migrate


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def repository(tmp_path):
    connection = connect(tmp_path / "auth.sqlite3")
    migrate(connection)
    connection.execute(
        "INSERT INTO users(id, created_at) VALUES (?, ?)",
        ("user-1", NOW.isoformat()),
    )
    yield AuthSessionsRepository(connection)
    connection.close()


def test_session_stores_digest_and_authenticates_until_expiry(repository) -> None:
    session = repository.create(
        user_id="user-1", now=NOW, expires_at=NOW + timedelta(hours=1)
    )
    row = repository.connection.execute(
        "SELECT token_hash FROM auth_sessions WHERE id = ?", (session.session_id,)
    ).fetchone()

    assert row["token_hash"] != session.raw_token
    assert repository.get_active_user_id(raw_token=session.raw_token, now=NOW) == "user-1"
    assert (
        repository.get_active_user_id(
            raw_token=session.raw_token, now=NOW + timedelta(hours=1)
        )
        is None
    )


def test_revoked_session_cannot_authenticate(repository) -> None:
    session = repository.create(
        user_id="user-1", now=NOW, expires_at=NOW + timedelta(hours=1)
    )

    assert repository.revoke(raw_token=session.raw_token, now=NOW + timedelta(minutes=1))
    assert not repository.revoke(raw_token=session.raw_token, now=NOW + timedelta(minutes=2))
    assert repository.get_active_user_id(raw_token=session.raw_token, now=NOW) is None


def test_session_creation_rejects_naive_or_past_expiry(repository) -> None:
    with pytest.raises(ValueError, match="timezone"):
        repository.create(
            user_id="user-1",
            now=datetime(2026, 10, 12, 12, 0),
            expires_at=NOW + timedelta(hours=1),
        )

    with pytest.raises(ValueError, match="after creation"):
        repository.create(user_id="user-1", now=NOW, expires_at=NOW)

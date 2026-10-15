from datetime import UTC, datetime, timedelta
import sqlite3

from app.application.auth_sessions_service import AuthSessionsService
from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.connection import migrate


def test_revoked_session_cannot_be_resolved_again() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    try:
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("user-1", "2026-10-15T10:00:00Z"),
        )
        connection.commit()
        sessions = AuthSessionsService(AuthSessionsRepository(connection))
        now = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
        session = sessions.create(user_id="user-1", now=now)

        assert sessions.resolve(raw_token=session.raw_token, now=now) == "user-1"
        assert sessions.revoke(raw_token=session.raw_token, now=now + timedelta(seconds=1))
        assert sessions.resolve(
            raw_token=session.raw_token, now=now + timedelta(seconds=2)
        ) is None
        assert not sessions.revoke(
            raw_token=session.raw_token, now=now + timedelta(seconds=3)
        )
    finally:
        connection.close()


def test_expired_session_cannot_be_resolved() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    try:
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("user-1", "2026-10-15T10:00:00Z"),
        )
        connection.commit()
        sessions = AuthSessionsService(
            AuthSessionsRepository(connection), lifetime=timedelta(minutes=5)
        )
        now = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
        session = sessions.create(user_id="user-1", now=now)

        assert sessions.resolve(
            raw_token=session.raw_token, now=session.expires_at - timedelta(microseconds=1)
        ) == "user-1"
        assert sessions.resolve(raw_token=session.raw_token, now=session.expires_at) is None
    finally:
        connection.close()

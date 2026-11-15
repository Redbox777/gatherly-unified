from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.connection import connect
from app.interfaces.web.constants import SESSION_COOKIE
from app.interfaces.web.http_api import create_app


NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def _client_with_identity(tmp_path, monkeypatch, *, provider: str):
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "gatherly_test_bot")
    database_path = tmp_path / f"telegram-{provider}.sqlite3"
    app = create_app(database_path)
    connection = connect(database_path)
    try:
        identity = AuthIdentitiesRepository(connection).create_user_with_identity(
            provider=provider,
            provider_subject="123456" if provider == "telegram" else "person@example.test",
            password_hash="test-password-hash" if provider == "email" else None,
            now=NOW,
            verified_at=NOW,
        )
        session_now = datetime.now(UTC)
        session = AuthSessionsRepository(connection).create(
            user_id=identity.user_id,
            now=session_now,
            expires_at=session_now + timedelta(days=1),
        )
    finally:
        connection.close()
    client = TestClient(app, headers={"Origin": "http://testserver"})
    client.cookies.set(SESSION_COOKIE, session.raw_token, domain="testserver", path="/")
    return client, database_path


def test_telegram_preference_requires_linked_identity_and_exposes_bot_link(
    tmp_path, monkeypatch
) -> None:
    client, database_path = _client_with_identity(tmp_path, monkeypatch, provider="telegram")

    preferences = client.get("/notification-preferences")

    assert preferences.status_code == 200
    assert preferences.json()["telegram_linked"] is True
    assert preferences.json()["telegram_available"] is True
    assert preferences.json()["telegram_bot_url"] == "https://t.me/gatherly_test_bot"

    updated = client.put(
        "/notification-preferences",
        json={
            "email_enabled": True,
            "event_updates_enabled": True,
            "participant_added_enabled": True,
            "reminders_enabled": True,
            "telegram_enabled": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["telegram_enabled"] is True
    connection = connect(database_path)
    try:
        consent = connection.execute(
            """SELECT telegram_enabled, telegram_consent_at,
                      telegram_consent_version
               FROM notification_preferences"""
        ).fetchone()
        assert consent["telegram_enabled"] == 1
        assert consent["telegram_consent_at"] is not None
        assert consent["telegram_consent_version"] == "telegram-v1"
    finally:
        connection.close()


def test_telegram_preference_cannot_be_enabled_without_telegram_identity(
    tmp_path, monkeypatch
) -> None:
    client, _ = _client_with_identity(tmp_path, monkeypatch, provider="email")

    updated = client.put(
        "/notification-preferences",
        json={
            "email_enabled": True,
            "event_updates_enabled": True,
            "participant_added_enabled": True,
            "reminders_enabled": True,
            "telegram_enabled": True,
        },
    )

    assert updated.status_code == 409

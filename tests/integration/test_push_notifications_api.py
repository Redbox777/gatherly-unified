import base64
from datetime import UTC, datetime, timedelta
import os

from fastapi.testclient import TestClient

from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.auth_sessions_repository import AuthSessionsRepository
from app.infrastructure.database.connection import connect
from app.interfaces.web.constants import SESSION_COOKIE
from app.interfaces.web.http_api import create_app


P256DH = base64.urlsafe_b64encode(os.urandom(65)).decode("ascii").rstrip("=")
AUTH = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii").rstrip("=")
SUBSCRIPTION_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
PUBLIC_VAPID_KEY = base64.urlsafe_b64encode(os.urandom(65)).decode("ascii").rstrip("=")


def _authenticated_client(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("WEB_PUSH_VAPID_PUBLIC_KEY", PUBLIC_VAPID_KEY)
    monkeypatch.setenv("WEB_PUSH_VAPID_PRIVATE_KEY", "private-vapid-key")
    monkeypatch.setenv("WEB_PUSH_VAPID_SUBJECT", "mailto:ops@example.test")
    monkeypatch.setenv("WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY", SUBSCRIPTION_KEY)
    database_path = tmp_path / "push-api.sqlite3"
    app = create_app(database_path)
    connection = connect(database_path)
    try:
        now = datetime.now(UTC)
        identity = AuthIdentitiesRepository(connection).create_user_with_identity(
            provider="email",
            provider_subject="push@example.test",
            password_hash="test-password-hash",
            now=now,
            verified_at=now,
        )
        session = AuthSessionsRepository(connection).create(
            user_id=identity.user_id,
            now=now,
            expires_at=now + timedelta(days=1),
        )
    finally:
        connection.close()
    client = TestClient(app, headers={"Origin": "http://testserver"})
    client.cookies.set(SESSION_COOKIE, session.raw_token)
    return client, database_path


def test_push_subscription_requires_auth_and_can_be_enabled_and_revoked(
    tmp_path, monkeypatch
) -> None:
    client, database_path = _authenticated_client(tmp_path, monkeypatch)
    public_key = client.get("/push/public-key")
    assert public_key.status_code == 200
    assert public_key.json() == {"public_key": PUBLIC_VAPID_KEY}

    subscription = {
        "endpoint": "https://fcm.googleapis.com/send/private-endpoint",
        "keys": {"p256dh": P256DH, "auth": AUTH},
    }
    created = client.post("/push-subscriptions", json=subscription)
    assert created.status_code == 201
    assert created.json() == {"push_enabled": True}
    preferences = client.get("/notification-preferences")
    assert preferences.status_code == 200
    assert preferences.json()["push_enabled"] is True
    assert preferences.json()["push_available"] is True

    connection = connect(database_path)
    try:
        stored = connection.execute(
            "SELECT subscription_ciphertext FROM push_subscriptions"
        ).fetchone()
        assert subscription["endpoint"].encode() not in bytes(
            stored["subscription_ciphertext"]
        )
        consent = connection.execute(
            """SELECT push_consent_at, push_consent_version
               FROM notification_preferences"""
        ).fetchone()
        assert consent["push_consent_at"] is not None
        assert consent["push_consent_version"] == "push-v1"
    finally:
        connection.close()

    deleted = client.delete("/push-subscriptions")
    assert deleted.status_code == 200
    assert deleted.json()["push_enabled"] is False
    assert client.get("/notification-preferences").json()["push_enabled"] is False


def test_push_subscription_rejects_invalid_endpoint(tmp_path, monkeypatch) -> None:
    client, _ = _authenticated_client(tmp_path, monkeypatch)

    response = client.post(
        "/push-subscriptions",
        json={
            "endpoint": "http://push.example.test/insecure",
            "keys": {"p256dh": P256DH, "auth": AUTH},
        },
    )

    assert response.status_code == 422


def test_push_public_key_requires_authentication(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("WEB_PUSH_VAPID_PUBLIC_KEY", PUBLIC_VAPID_KEY)
    monkeypatch.setenv("WEB_PUSH_VAPID_PRIVATE_KEY", "private-vapid-key")
    monkeypatch.setenv("WEB_PUSH_VAPID_SUBJECT", "mailto:ops@example.test")
    monkeypatch.setenv("WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY", SUBSCRIPTION_KEY)
    app = create_app(tmp_path / "push-unauthenticated.sqlite3")

    with TestClient(app) as client:
        response = client.get("/push/public-key")

    assert response.status_code == 401

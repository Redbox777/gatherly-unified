from datetime import UTC, datetime, timedelta
import json

from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.infrastructure.database.connection import connect
from app.interfaces.web.constants import SESSION_COOKIE
from app.interfaces.web.http_api import create_app


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send_text(self, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


def _verified_client(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "false")
    sender = FakeEmailSender()
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    database_path = tmp_path / "privacy-api.sqlite3"
    app = create_app(database_path)
    client = TestClient(app, headers={"Origin": "http://testserver"})
    registration = client.post(
        "/auth/register",
        json={
            "email": "privacy@example.test",
            "password": "correct horse battery staple",
        },
    )
    assert registration.status_code == 202
    token = sender.messages[0][2].split("token=", maxsplit=1)[1].splitlines()[0]
    verification = client.post("/auth/verify-email", json={"token": token})
    assert verification.status_code == 200
    login = client.post(
        "/auth/login",
        json={
            "email": "privacy@example.test",
            "password": "correct horse battery staple",
        },
    )
    assert login.status_code == 200
    return client, database_path


def test_account_export_contains_user_data_but_not_credentials_or_bearer_hashes(
    tmp_path, monkeypatch
) -> None:
    client, _ = _verified_client(tmp_path, monkeypatch)
    event = client.post("/events", json={"title": "Private planning"})
    assert event.status_code == 201

    response = client.get("/account/export")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["export_format"] == "gatherly-account-export-v1"
    assert payload["events"][0]["title"] == "Private planning"
    assert payload["identities"][0]["provider"] == "email"
    serialized = json.dumps(payload).casefold()
    assert "password_hash" not in serialized
    assert "token_hash" not in serialized
    assert "correct horse battery staple" not in serialized


def test_account_deletion_requires_confirmation_and_fresh_login(
    tmp_path, monkeypatch
) -> None:
    client, database_path = _verified_client(tmp_path, monkeypatch)
    event = client.post("/events", json={"title": "Delete this event"})
    assert event.status_code == 201

    missing_confirmation = client.delete("/account")
    assert missing_confirmation.status_code == 400
    assert client.get("/auth/me").status_code == 200

    connection = connect(database_path)
    try:
        connection.execute(
            "UPDATE auth_sessions SET created_at = ?",
            ((datetime.now(UTC) - timedelta(hours=1)).isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()

    stale_session = client.delete(
        "/account",
        headers={"x-account-deletion-confirmation": "DELETE"},
    )
    assert stale_session.status_code == 403
    assert client.get("/auth/me").status_code == 200


def test_account_deletion_erases_owned_data_and_revokes_cookie(
    tmp_path, monkeypatch
) -> None:
    client, database_path = _verified_client(tmp_path, monkeypatch)
    event = client.post("/events", json={"title": "Account-owned event"})
    assert event.status_code == 201
    assert client.get("/account/export").status_code == 200

    deletion = client.delete(
        "/account",
        headers={"x-account-deletion-confirmation": "DELETE"},
    )

    assert deletion.status_code == 204
    assert client.get("/auth/me").status_code == 401
    assert client.cookies.get(SESSION_COOKIE) is None

    connection = connect(database_path)
    try:
        for table in (
            "users",
            "auth_identities",
            "auth_sessions",
            "auth_email_tokens",
            "events",
            "notifications",
            "notification_preferences",
            "event_notes",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        connection.close()


def test_account_export_requires_authentication(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "false")
    app = create_app(tmp_path / "privacy-unauthenticated.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.get("/account/export")

    assert response.status_code == 401

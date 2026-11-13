from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
import sqlite3

from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.domain.auth_tokens import hash_token
from app.interfaces.web.http_api import create_app


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send_text(self, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


def _register_and_verify(client: TestClient, sender: FakeEmailSender) -> None:
    registration = client.post(
        "/auth/register",
        json={
            "email": "session-contract@example.test",
            "password": "correct horse battery staple",
        },
    )
    assert registration.status_code == 202
    token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
    verification = client.post("/auth/verify-email", json={"token": token})
    assert verification.status_code == 200


def _login(client: TestClient) -> str:
    response = client.post(
        "/auth/login",
        json={
            "email": "session-contract@example.test",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200
    raw_token = response.cookies.get("gatherly_session")
    assert raw_token
    return raw_token


def test_login_cookie_matches_server_session_lifetime_and_security_attributes(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    database_path = tmp_path / "session-cookie-contract.sqlite3"
    app = create_app(database_path)

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _register_and_verify(client, sender)
        login = client.post(
            "/auth/login",
            json={
                "email": "session-contract@example.test",
                "password": "correct horse battery staple",
            },
        )

    assert login.status_code == 200
    raw_token = login.cookies["gatherly_session"]
    cookie = SimpleCookie()
    cookie.load(login.headers["set-cookie"])
    session_cookie = cookie["gatherly_session"]

    assert session_cookie["httponly"]
    assert session_cookie["samesite"].lower() == "lax"
    assert session_cookie["path"] == "/"
    assert not session_cookie["secure"]
    assert session_cookie["max-age"] == str(14 * 24 * 60 * 60)

    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT created_at, expires_at FROM auth_sessions WHERE token_hash = ?",
            (hash_token(raw_token),),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    created_at = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(row[1].replace("Z", "+00:00"))
    assert int((expires_at - created_at).total_seconds()) == int(
        session_cookie["max-age"]
    )


def test_logout_revokes_server_session_and_expires_browser_cookie(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "session-logout-contract.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _register_and_verify(client, sender)
        raw_token = _login(client)

        logout = client.post("/auth/logout")
        assert logout.status_code == 200

        cookie = SimpleCookie()
        cookie.load(logout.headers["set-cookie"])
        cleared_cookie = cookie["gatherly_session"]
        assert cleared_cookie["max-age"] == "0"
        assert cleared_cookie["httponly"]
        assert cleared_cookie["samesite"].lower() == "lax"
        assert cleared_cookie["path"] == "/"

        # Re-send the pre-logout bearer token explicitly: deleting the browser
        # cookie alone must not be what makes the next request unauthorized.
        replay = client.get(
            "/auth/me", cookies={"gatherly_session": raw_token}
        )
        assert replay.status_code == 401


def test_http_authentication_rejects_a_session_expired_in_the_database(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    database_path = tmp_path / "session-expiry-contract.sqlite3"
    app = create_app(database_path)

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _register_and_verify(client, sender)
        raw_token = _login(client)

        now = datetime.now(UTC)
        created_at = (now - timedelta(days=15)).isoformat().replace("+00:00", "Z")
        expired_at = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        connection = sqlite3.connect(database_path)
        try:
            cursor = connection.execute(
                """
                UPDATE auth_sessions
                SET created_at = ?, expires_at = ?
                WHERE token_hash = ?
                """,
                (created_at, expired_at, hash_token(raw_token)),
            )
            connection.commit()
        finally:
            connection.close()

        assert cursor.rowcount == 1
        response = client.get(
            "/auth/me", cookies={"gatherly_session": raw_token}
        )
        assert response.status_code == 401

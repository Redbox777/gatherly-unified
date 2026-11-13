from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.interfaces.web.http_api import create_app


EMAIL = "password-session-contract@example.test"
OLD_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a different secure password"


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send_text(self, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


def _register_and_verify(client: TestClient, sender: FakeEmailSender) -> None:
    registration = client.post(
        "/auth/register",
        json={"email": EMAIL, "password": OLD_PASSWORD},
    )
    assert registration.status_code == 202
    token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
    verification = client.post("/auth/verify-email", json={"token": token})
    assert verification.status_code == 200


def _login(client: TestClient, password: str) -> str:
    response = client.post(
        "/auth/login",
        json={"email": EMAIL, "password": password},
    )
    assert response.status_code == 200
    raw_token = response.cookies.get("gatherly_session")
    assert raw_token
    return raw_token


def test_password_change_revokes_every_existing_http_session(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "password-change-sessions.sqlite3")

    with TestClient(app) as client:
        _register_and_verify(client, sender)
        first_session = _login(client, OLD_PASSWORD)
        second_session = _login(client, OLD_PASSWORD)

        changed = client.post(
            "/auth/password/change",
            cookies={"gatherly_session": second_session},
            json={
                "current_password": OLD_PASSWORD,
                "new_password": NEW_PASSWORD,
            },
        )
        assert changed.status_code == 200

        for old_session in (first_session, second_session):
            response = client.get(
                "/auth/me", cookies={"gatherly_session": old_session}
            )
            assert response.status_code == 401

        old_password_login = client.post(
            "/auth/login",
            json={"email": EMAIL, "password": OLD_PASSWORD},
        )
        assert old_password_login.status_code == 401
        assert _login(client, NEW_PASSWORD)


def test_password_reset_revokes_existing_http_session_and_allows_new_password(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "password-reset-sessions.sqlite3")

    with TestClient(app) as client:
        _register_and_verify(client, sender)
        old_session = _login(client, OLD_PASSWORD)

        requested = client.post(
            "/auth/password-reset/request",
            json={"email": EMAIL},
        )
        assert requested.status_code == 202
        assert len(sender.messages) == 2

        reset_body = sender.messages[-1][2]
        reset_link = next(
            line for line in reset_body.splitlines() if "token=" in line
        )
        parsed_reset_link = urlsplit(reset_link)
        assert not parsed_reset_link.query
        reset_token = parse_qs(parsed_reset_link.fragment)["token"][0]

        confirmed = client.post(
            "/auth/password-reset/confirm",
            json={"token": reset_token, "new_password": NEW_PASSWORD},
        )
        assert confirmed.status_code == 200

        old_session_response = client.get(
            "/auth/me", cookies={"gatherly_session": old_session}
        )
        assert old_session_response.status_code == 401

        old_password_login = client.post(
            "/auth/login",
            json={"email": EMAIL, "password": OLD_PASSWORD},
        )
        assert old_password_login.status_code == 401
        assert _login(client, NEW_PASSWORD)

        replayed_reset = client.post(
            "/auth/password-reset/confirm",
            json={"token": reset_token, "new_password": OLD_PASSWORD},
        )
        assert replayed_reset.status_code == 400

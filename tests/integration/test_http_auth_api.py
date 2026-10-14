from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.interfaces.web.http_api import create_app


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send_text(self, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


def test_health_endpoint_uses_temporary_database(tmp_path) -> None:
    app = create_app(tmp_path / "http-auth.sqlite3")
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_password_reset_request_has_generic_response(tmp_path) -> None:
    app = create_app(tmp_path / "reset-http.sqlite3")
    with TestClient(app) as client:
        first = client.post(
            "/auth/password-reset/request", json={"email": "unknown@example.test"}
        )
        second = client.post(
            "/auth/password-reset/request", json={"email": "missing@example.test"}
        )
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


def test_registration_verification_login_and_logout_flow(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "auth-flow.sqlite3")

    with TestClient(app) as client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "person@example.test",
                "password": "correct horse battery staple",
            },
        )
        assert registration.status_code == 202
        assert len(sender.messages) == 1
        assert sender.messages[0][0] == "person@example.test"

        before_verification = client.post(
            "/auth/login",
            json={
                "email": "person@example.test",
                "password": "correct horse battery staple",
            },
        )
        assert before_verification.status_code == 401

        email_body = sender.messages[0][2]
        token = email_body.split("token=", maxsplit=1)[1].splitlines()[0]
        verification = client.post("/auth/verify-email", json={"token": token})
        assert verification.status_code == 200

        login = client.post(
            "/auth/login",
            json={
                "email": "person@example.test",
                "password": "correct horse battery staple",
            },
        )
        assert login.status_code == 200
        assert "gatherly_session" in login.cookies
        assert login.cookies.get("gatherly_session")

        current_user = client.get("/auth/me")
        assert current_user.status_code == 200
        user_id = current_user.json()["user_id"]
        assert user_id

        logout = client.post("/auth/logout")
        assert logout.status_code == 200
        after_logout = client.get("/auth/me")
        assert after_logout.status_code == 401

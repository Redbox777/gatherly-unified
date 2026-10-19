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
    monkeypatch.setenv("COOKIE_SECURE", "false")
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
        replayed_verification = client.post(
            "/auth/verify-email", json={"token": token}
        )
        assert replayed_verification.status_code == 400

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
        invalid_session = client.get(
            "/auth/me", cookies={"gatherly_session": "forged-session-token"}
        )
        assert invalid_session.status_code == 401


def test_password_reset_http_flow_invalidates_old_password(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "password-reset-http.sqlite3")

    with TestClient(app) as client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "reset@example.test",
                "password": "original password long enough",
            },
        )
        assert registration.status_code == 202
        verification_body = sender.messages[-1][2]
        verification_token = verification_body.split("token=", maxsplit=1)[1].splitlines()[0]
        verification = client.post(
            "/auth/verify-email", json={"token": verification_token}
        )
        assert verification.status_code == 200

        existing_login = client.post(
            "/auth/login",
            json={
                "email": "reset@example.test",
                "password": "original password long enough",
            },
        )
        assert existing_login.status_code == 200
        assert client.get("/auth/me").status_code == 200

        request_reset = client.post(
            "/auth/password-reset/request",
            json={"email": "reset@example.test"},
        )
        assert request_reset.status_code == 202
        assert request_reset.json() == {
            "message": (
                "If an eligible account exists for that email address, "
                "password-reset instructions will be sent."
            )
        }
        assert len(sender.messages) == 2

        reset_body = sender.messages[-1][2]
        reset_token = reset_body.split("token=", maxsplit=1)[1].splitlines()[0]
        confirmation = client.post(
            "/auth/password-reset/confirm",
            json={
                "token": reset_token,
                "new_password": "replacement password long enough",
            },
        )
        assert confirmation.status_code == 200
        assert client.get("/auth/me").status_code == 401
        replayed_reset = client.post(
            "/auth/password-reset/confirm",
            json={
                "token": reset_token,
                "new_password": "another replacement password",
            },
        )
        assert replayed_reset.status_code == 400

        old_password_login = client.post(
            "/auth/login",
            json={
                "email": "reset@example.test",
                "password": "original password long enough",
            },
        )
        new_password_login = client.post(
            "/auth/login",
            json={
                "email": "reset@example.test",
                "password": "replacement password long enough",
            },
        )
        assert old_password_login.status_code == 401
        assert new_password_login.status_code == 200


def test_authenticated_password_change_revokes_all_sessions(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "password-change-http.sqlite3")

    with TestClient(app) as client, TestClient(app) as other_client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "change@example.test",
                "password": "original password long enough",
            },
        )
        assert registration.status_code == 202
        verification_token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert client.post(
            "/auth/verify-email", json={"token": verification_token}
        ).status_code == 200

        credentials = {
            "email": "change@example.test",
            "password": "original password long enough",
        }
        assert client.post("/auth/login", json=credentials).status_code == 200
        assert other_client.post("/auth/login", json=credentials).status_code == 200
        assert client.get("/auth/me").status_code == 200
        assert other_client.get("/auth/me").status_code == 200

        wrong_password = client.post(
            "/auth/password/change",
            json={
                "current_password": "incorrect current password",
                "new_password": "replacement password long enough",
            },
        )
        assert wrong_password.status_code == 400
        assert client.get("/auth/me").status_code == 200
        assert other_client.get("/auth/me").status_code == 200

        changed = client.post(
            "/auth/password/change",
            json={
                "current_password": credentials["password"],
                "new_password": "replacement password long enough",
            },
        )
        assert changed.status_code == 200
        assert changed.json() == {"message": "Password updated; sign in again"}
        set_cookie = changed.headers.get("set-cookie", "")
        assert "gatherly_session=" in set_cookie
        assert "Max-Age=0" in set_cookie
        assert client.get("/auth/me").status_code == 401
        assert other_client.get("/auth/me").status_code == 401

        old_password_login = client.post("/auth/login", json=credentials)
        new_password_login = client.post(
            "/auth/login",
            json={
                "email": "change@example.test",
                "password": "replacement password long enough",
            },
        )
        assert old_password_login.status_code == 401
        assert new_password_login.status_code == 200


def test_password_change_requires_authentication(tmp_path) -> None:
    app = create_app(tmp_path / "password-change-unauthenticated.sqlite3")
    with TestClient(app) as client:
        response = client.post(
            "/auth/password/change",
            json={
                "current_password": "old password long enough",
                "new_password": "replacement password long enough",
            },
        )
    assert response.status_code == 401


def test_auth_me_rejects_missing_and_forged_session_tokens(tmp_path) -> None:
    app = create_app(tmp_path / "invalid-session.sqlite3")
    with TestClient(app) as client:
        missing = client.get("/auth/me")
        forged = client.get(
            "/auth/me", cookies={"gatherly_session": "not-a-real-session"}
        )

    assert missing.status_code == 401
    assert forged.status_code == 401


def test_state_changing_auth_request_rejects_cross_origin(tmp_path) -> None:
    app = create_app(tmp_path / "csrf-origin.sqlite3")
    with TestClient(app) as client:
        response = client.post(
            "/auth/logout",
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin request rejected"}


def test_state_changing_auth_request_allows_same_origin(tmp_path) -> None:
    app = create_app(tmp_path / "same-origin.sqlite3")
    with TestClient(app) as client:
        response = client.post(
            "/auth/logout",
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 200


def test_login_rate_limit_returns_retry_after_header(tmp_path) -> None:
    app = create_app(tmp_path / "login-rate-limit.sqlite3")
    with TestClient(app) as client:
        responses = [
            client.post(
                "/auth/login",
                json={"email": "nobody@example.test", "password": "wrong password"},
            )
            for _ in range(6)
        ]

    assert [response.status_code for response in responses] == [
        401,
        401,
        401,
        401,
        401,
        429,
    ]
    assert responses[-1].headers["retry-after"] == "60"
    assert responses[-1].json() == {
        "detail": "Too many requests; try again later"
    }


def test_email_verification_endpoint_is_rate_limited(tmp_path) -> None:
    app = create_app(tmp_path / "verification-rate-limit.sqlite3")
    with TestClient(app) as client:
        responses = [
            client.post("/auth/verify-email", json={"token": "invalid-token"})
            for _ in range(6)
        ]

    assert [response.status_code for response in responses] == [
        400,
        400,
        400,
        400,
        400,
        429,
    ]


def test_authenticated_user_can_create_and_retrieve_owned_event(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "events-http.sqlite3")
    with TestClient(app) as client:
        registration = client.post("/auth/register", json={"email": "event@example.test", "password": "original password long enough"})
        assert registration.status_code == 202
        token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
        assert client.post("/auth/login", json={"email": "event@example.test", "password": "original password long enough"}).status_code == 200
        created = client.post("/events", json={"title": "Weekend trip"})
        assert created.status_code == 201
        body = created.json()
        assert body["title"] == "Weekend trip"
        assert body["id"]
        fetched = client.get(f"/events/{body['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == body["id"]


def test_event_routes_require_authentication_and_hide_other_owners(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "event-ownership-http.sqlite3")
    with TestClient(app) as client:
        assert client.post("/events", json={"title": "No session"}).status_code == 401
        assert client.get("/events/00000000-0000-0000-0000-000000000001").status_code == 401

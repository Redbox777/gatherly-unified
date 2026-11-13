from concurrent.futures import ThreadPoolExecutor

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
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_password_reset_request_has_generic_response(tmp_path) -> None:
    app = create_app(tmp_path / "reset-http.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        first = client.post(
            "/auth/password-reset/request", json={"email": "unknown@example.test"}
        )
        second = client.post(
            "/auth/password-reset/request", json={"email": "missing@example.test"}
        )
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


def test_registration_email_failure_logs_without_sensitive_details(
    tmp_path, monkeypatch, caplog
) -> None:
    class FailingEmailSender:
        def send_text(self, recipient: str, subject: str, body: str) -> None:
            raise OSError(f"SMTP unavailable for {recipient}")

    monkeypatch.setattr(http_api, "_email_sender", FailingEmailSender)
    app = create_app(tmp_path / "registration-email-failure.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.post(
            "/auth/register",
            json={
                "email": "private-person@example.test",
                "password": "correct horse battery staple",
            },
        )

    assert response.status_code == 202
    assert response.json() == http_api.GENERIC_REGISTRATION_RESPONSE
    assert "Registration verification email delivery failed (OSError)" in caplog.text
    assert "private-person@example.test" not in caplog.text
    assert "SMTP unavailable" not in caplog.text


def test_registration_response_does_not_reveal_existing_accounts(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "registration-enumeration.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        new_account = client.post(
            "/auth/register",
            json={"email": "enumeration@example.test", "password": "correct horse battery staple"},
        )
        duplicate_unverified = client.post(
            "/auth/register",
            json={"email": "enumeration@example.test", "password": "another secure password"},
        )
        token = sender.messages[0][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
        duplicate_verified = client.post(
            "/auth/register",
            json={"email": "enumeration@example.test", "password": "yet another secure password"},
        )
        unknown = client.post(
            "/auth/register",
            json={"email": "brand-new@example.test", "password": "correct horse battery staple"},
        )

    responses = (new_account, duplicate_unverified, duplicate_verified, unknown)
    assert all(response.status_code == 202 for response in responses)
    assert all(response.json() == http_api.GENERIC_REGISTRATION_RESPONSE for response in responses)
    assert len(sender.messages) == 2


def test_registration_can_recover_after_email_delivery_failure(
    tmp_path, monkeypatch
) -> None:
    class FailOnceEmailSender:
        def __init__(self) -> None:
            self.messages: list[tuple[str, str, str]] = []
            self.fail_next = True

        def send_text(self, recipient: str, subject: str, body: str) -> None:
            if self.fail_next:
                self.fail_next = False
                raise OSError("temporary SMTP outage")
            self.messages.append((recipient, subject, body))

    sender = FailOnceEmailSender()
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "verification-resend.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "retry@example.test",
                "password": "correct horse battery staple",
            },
        )
        assert registration.status_code == 202
        assert registration.json() == http_api.GENERIC_REGISTRATION_RESPONSE

        resend = client.post(
            "/auth/verification/request", json={"email": "retry@example.test"}
        )
        assert resend.status_code == 202
        assert len(sender.messages) == 1
        assert sender.messages[0][0] == "retry@example.test"

        token = sender.messages[0][2].split("token=", maxsplit=1)[1].splitlines()[0]
        verification = client.post("/auth/verify-email", json={"token": token})
        assert verification.status_code == 200

        unknown = client.post(
            "/auth/verification/request", json={"email": "unknown@example.test"}
        )
        assert unknown.status_code == 202
        assert unknown.json() == resend.json()


def test_verification_resend_keeps_generic_response_when_delivery_fails(
    tmp_path, monkeypatch, caplog
) -> None:
    class ToggleEmailSender:
        def __init__(self) -> None:
            self.fail = False
            self.messages: list[tuple[str, str, str]] = []

        def send_text(self, recipient: str, subject: str, body: str) -> None:
            if self.fail:
                raise OSError(f"SMTP unavailable for {recipient}")
            self.messages.append((recipient, subject, body))

    sender = ToggleEmailSender()
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "verification-resend-failure.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "resend-private@example.test",
                "password": "correct horse battery staple",
            },
        )
        assert registration.status_code == 202
        original_token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]

        sender.fail = True
        failed_delivery = client.post(
            "/auth/verification/request",
            json={"email": "resend-private@example.test"},
        )
        unknown_account = client.post(
            "/auth/verification/request",
            json={"email": "unknown@example.test"},
        )
        previous_token_still_works = client.post(
            "/auth/verify-email", json={"token": original_token}
        )

    assert previous_token_still_works.status_code == 200
    assert failed_delivery.status_code == unknown_account.status_code == 202
    assert failed_delivery.json() == unknown_account.json()
    assert "Registration verification email delivery failed (OSError)" in caplog.text
    assert "resend-private@example.test" not in caplog.text
    assert "SMTP unavailable" not in caplog.text


def test_resending_verification_invalidates_previous_token(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "verification-token-rotation.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "rotate@example.test",
                "password": "correct horse battery staple",
            },
        )
        assert registration.status_code == 202
        first_token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]

        resend = client.post(
            "/auth/verification/request", json={"email": "rotate@example.test"}
        )
        assert resend.status_code == 202
        second_token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert second_token != first_token

        old_verification = client.post(
            "/auth/verify-email", json={"token": first_token}
        )
        assert old_verification.status_code == 400

        new_verification = client.post(
            "/auth/verify-email", json={"token": second_token}
        )
        assert new_verification.status_code == 200


def test_registration_verification_login_and_logout_flow(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "auth-flow.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
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
        assert "http://localhost:8000/verify-email#token=" in sender.messages[0][2]
        assert "?token=" not in sender.messages[0][2]

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



def test_auth_token_landing_pages_are_same_origin_and_do_not_expose_fragments(
    tmp_path,
) -> None:
    app = create_app(tmp_path / "auth-token-pages.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        verification = client.get("/verify-email")
        reset = client.get("/reset-password")

    for response in (verification, reset):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert 'window.location.hash.slice(1)' in response.text
        assert "window.history.replaceState" in response.text
        assert 'method: "POST"' in response.text

    assert 'fetch("/auth/verify-email"' in verification.text
    assert 'fetch("/auth/password-reset/confirm"' in reset.text
    assert "new_password: password.value" in reset.text

def test_password_reset_delivery_failure_logs_safely_and_keeps_generic_response(
    tmp_path, monkeypatch, caplog
) -> None:
    class ToggleEmailSender:
        def __init__(self) -> None:
            self.fail = False
            self.messages: list[tuple[str, str, str]] = []

        def send_text(self, recipient: str, subject: str, body: str) -> None:
            if self.fail:
                raise OSError(f"SMTP unavailable for {recipient}")
            self.messages.append((recipient, subject, body))

    sender = ToggleEmailSender()
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "password-reset-delivery-failure.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "reset-private@example.test",
                "password": "correct horse battery staple",
            },
        )
        assert registration.status_code == 202
        verification_token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert client.post(
            "/auth/verify-email", json={"token": verification_token}
        ).status_code == 200

        sender.fail = True
        failed_delivery = client.post(
            "/auth/password-reset/request",
            json={"email": "reset-private@example.test"},
        )
        unknown_account = client.post(
            "/auth/password-reset/request",
            json={"email": "unknown@example.test"},
        )

    assert failed_delivery.status_code == unknown_account.status_code == 202
    assert failed_delivery.json() == unknown_account.json()
    assert "Password reset email delivery failed (OSError)" in caplog.text
    assert "reset-private@example.test" not in caplog.text
    assert "SMTP unavailable" not in caplog.text


def test_password_reset_http_flow_invalidates_old_password(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "password-reset-http.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
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

        first_reset_body = sender.messages[-1][2]
        first_reset_token = first_reset_body.split("token=", maxsplit=1)[1].splitlines()[0]
        second_request = client.post(
            "/auth/password-reset/request",
            json={"email": "reset@example.test"},
        )
        assert second_request.status_code == 202
        assert second_request.json() == request_reset.json()
        assert len(sender.messages) == 3

        reset_body = sender.messages[-1][2]
        reset_token = reset_body.split("token=", maxsplit=1)[1].splitlines()[0]
        stale_confirmation = client.post(
            "/auth/password-reset/confirm",
            json={
                "token": first_reset_token,
                "new_password": "stale replacement password",
            },
        )
        assert stale_confirmation.status_code == 400

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

    with (

        TestClient(app, headers={"Origin": "http://testserver"}) as client,

        TestClient(app, headers={"Origin": "http://testserver"}) as other_client,

    ):
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
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
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
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        missing = client.get("/auth/me")
        forged = client.get(
            "/auth/me", cookies={"gatherly_session": "not-a-real-session"}
        )

    assert missing.status_code == 401
    assert forged.status_code == 401


def test_state_changing_auth_request_rejects_cross_origin(tmp_path) -> None:
    app = create_app(tmp_path / "csrf-origin.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.post(
            "/auth/logout",
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin request rejected"}


def test_state_changing_auth_request_allows_same_origin(tmp_path) -> None:
    app = create_app(tmp_path / "same-origin.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.post(
            "/auth/logout",
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 200


def test_cookie_authenticated_state_change_requires_origin_or_referer(
    tmp_path,
) -> None:
    app = create_app(tmp_path / "csrf-cookie-origin-required.sqlite3")
    with TestClient(app) as client:
        response = client.post(
            "/auth/logout",
            cookies={"gatherly_session": "session-token"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin request rejected"}


def test_cookie_authenticated_state_change_accepts_same_origin_referer(
    tmp_path,
) -> None:
    app = create_app(tmp_path / "csrf-cookie-referer.sqlite3")
    with TestClient(app) as client:
        response = client.post(
            "/auth/logout",
            cookies={"gatherly_session": "session-token"},
            headers={"Referer": "http://testserver/account"},
        )

    assert response.status_code == 200


def test_headerless_state_change_without_session_cookie_remains_allowed(
    tmp_path,
) -> None:
    app = create_app(tmp_path / "csrf-no-cookie.sqlite3")
    with TestClient(app) as client:
        response = client.post("/auth/logout")

    assert response.status_code == 200


def test_login_rate_limit_returns_retry_after_header(tmp_path) -> None:
    app = create_app(tmp_path / "login-rate-limit.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
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
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
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
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "event@example.test",
                "password": "original password long enough",
            },
        )
        assert registration.status_code == 202
        token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert client.post(
            "/auth/verify-email", json={"token": token}
        ).status_code == 200
        assert client.post(
            "/auth/login",
            json={
                "email": "event@example.test",
                "password": "original password long enough",
            },
        ).status_code == 200
        created = client.post("/events", json={"title": "Weekend trip"})
        assert created.status_code == 201
        body = created.json()
        assert body["title"] == "Weekend trip"
        assert body["id"]
        fetched = client.get(f"/events/{body['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == body["id"]


def test_event_routes_require_authentication_and_hide_other_owners(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "event-ownership-http.sqlite3")

    with (

        TestClient(app, headers={"Origin": "http://testserver"}) as owner,

        TestClient(app, headers={"Origin": "http://testserver"}) as other_user,

    ):
        assert owner.post("/events", json={"title": "No session"}).status_code == 401
        assert (
            owner.get("/events/00000000-0000-0000-0000-000000000001").status_code
            == 401
        )

        registration = owner.post(
            "/auth/register",
            json={
                "email": "owner@example.test",
                "password": "original password long enough",
            },
        )
        assert registration.status_code == 202
        owner_token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert owner.post(
            "/auth/verify-email", json={"token": owner_token}
        ).status_code == 200
        assert owner.post(
            "/auth/login",
            json={
                "email": "owner@example.test",
                "password": "original password long enough",
            },
        ).status_code == 200

        event = owner.post("/events", json={"title": "Private dinner"})
        assert event.status_code == 201
        event_id = event.json()["id"]
        participant = owner.post(
            f"/events/{event_id}/participants", json={"display_name": "Alex"}
        )
        assert participant.status_code == 201

        other_registration = other_user.post(
            "/auth/register",
            json={
                "email": "other@example.test",
                "password": "another password long enough",
            },
        )
        assert other_registration.status_code == 202
        other_token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert other_user.post(
            "/auth/verify-email", json={"token": other_token}
        ).status_code == 200
        assert other_user.post(
            "/auth/login",
            json={
                "email": "other@example.test",
                "password": "another password long enough",
            },
        ).status_code == 200

        assert other_user.get(f"/events/{event_id}").status_code == 404
        assert (
            other_user.post(
                f"/events/{event_id}/participants",
                json={"display_name": "Mallory"},
            ).status_code
            == 404
        )
        assert other_user.get(f"/events/{event_id}/participants").status_code == 404
        assert owner.get(f"/events/{event_id}/participants").json()[0]["display_name"] == "Alex"



def test_authenticated_owner_can_add_and_list_event_participants(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participants-http.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "participants@example.test",
                "password": "original password long enough",
            },
        )
        assert registration.status_code == 202
        token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert client.post(
            "/auth/verify-email", json={"token": token}
        ).status_code == 200
        assert client.post(
            "/auth/login",
            json={
                "email": "participants@example.test",
                "password": "original password long enough",
            },
        ).status_code == 200

        event_response = client.post("/events", json={"title": "Dinner"})
        assert event_response.status_code == 201
        event_id = event_response.json()["id"]

        added = client.post(
            f"/events/{event_id}/participants", json={"display_name": "  Alex  "}
        )
        assert added.status_code == 201
        participant = added.json()
        assert participant["display_name"] == "Alex"
        assert participant["event_id"] == event_id
        assert participant["id"]

        listed = client.get(f"/events/{event_id}/participants")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [participant["id"]]

        duplicate = client.post(
            f"/events/{event_id}/participants", json={"display_name": "Alex"}
        )
        assert duplicate.status_code == 409


def test_event_participant_routes_require_authentication(tmp_path) -> None:
    app = create_app(tmp_path / "participants-unauthenticated.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        event_id = "00000000-0000-0000-0000-000000000001"
        added = client.post(
            f"/events/{event_id}/participants", json={"display_name": "Alex"}
        )
        listed = client.get(f"/events/{event_id}/participants")
    assert added.status_code == 401
    assert listed.status_code == 401


def test_event_owner_can_update_and_delete_event(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "event-update-delete.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        assert client.post("/auth/register", json={
            "email": "event-edit@example.test",
            "password": "original password long enough",
        }).status_code == 202
        token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
        assert client.post("/auth/login", json={
            "email": "event-edit@example.test",
            "password": "original password long enough",
        }).status_code == 200
        created = client.post("/events", json={"title": "Old title"})
        event_id = created.json()["id"]
        updated = client.patch(f"/events/{event_id}", json={"title": "New title"})
        assert updated.status_code == 200
        assert updated.json()["title"] == "New title"
        assert client.get(f"/events/{event_id}").json()["title"] == "New title"
        assert client.delete(f"/events/{event_id}").status_code == 204
        assert client.get(f"/events/{event_id}").status_code == 404


def test_event_update_and_delete_are_owner_only(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "event-edit-owner-only.sqlite3")
    with (
        TestClient(app, headers={"Origin": "http://testserver"}) as owner,
        TestClient(app, headers={"Origin": "http://testserver"}) as other,
    ):
        for client, email, password in (
            (owner, "event-owner@example.test", "owner password long enough"),
            (other, "event-other@example.test", "other password long enough"),
        ):
            assert client.post("/auth/register", json={
                "email": email, "password": password,
            }).status_code == 202
            token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
            assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
            assert client.post("/auth/login", json={
                "email": email, "password": password,
            }).status_code == 200
        created = owner.post("/events", json={"title": "Owner event"})
        event_id = created.json()["id"]
        assert other.patch(f"/events/{event_id}", json={
            "title": "Hijacked",
        }).status_code == 404
        assert other.delete(f"/events/{event_id}").status_code == 404
        assert owner.get(f"/events/{event_id}").json()["title"] == "Owner event"
        assert owner.delete(f"/events/{event_id}").status_code == 204


def test_authenticated_user_can_list_only_their_own_events(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "event-list-http.sqlite3")

    with (

        TestClient(app, headers={"Origin": "http://testserver"}) as owner,

        TestClient(app, headers={"Origin": "http://testserver"}) as other,

    ):
        assert owner.get("/events").status_code == 401

        for client, email, password in (
            (owner, "list-owner@example.test", "owner password long enough"),
            (other, "list-other@example.test", "other password long enough"),
        ):
            assert client.post(
                "/auth/register", json={"email": email, "password": password}
            ).status_code == 202
            token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
            assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
            assert client.post(
                "/auth/login", json={"email": email, "password": password}
            ).status_code == 200

        first = owner.post("/events", json={"title": "First event"})
        second = owner.post("/events", json={"title": "Second event"})
        foreign = other.post("/events", json={"title": "Other user's event"})
        assert first.status_code == second.status_code == foreign.status_code == 201

        owner_events = owner.get("/events")
        assert owner_events.status_code == 200
        assert {event["id"] for event in owner_events.json()} == {
            first.json()["id"],
            second.json()["id"],
        }
        assert {event["title"] for event in owner_events.json()} == {
            "First event",
            "Second event",
        }
        assert all(event["title"] != "Other user's event" for event in owner_events.json())

        other_events = other.get("/events")
        assert other_events.status_code == 200
        assert [event["id"] for event in other_events.json()] == [foreign.json()["id"]]


def test_authenticated_user_with_no_events_gets_empty_list(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "event-list-empty-http.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        assert client.post(
            "/auth/register",
            json={
                "email": "empty-list@example.test",
                "password": "original password long enough",
            },
        ).status_code == 202
        token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
        assert client.post(
            "/auth/login",
            json={
                "email": "empty-list@example.test",
                "password": "original password long enough",
            },
        ).status_code == 200

        response = client.get("/events")
        assert response.status_code == 200
        assert response.json() == []


def test_event_owner_can_remove_participant(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-remove.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        email = "participant-remove@example.test"
        password = "original password long enough"
        assert client.post(
            "/auth/register", json={"email": email, "password": password}
        ).status_code == 202
        token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
        assert client.post(
            "/auth/login", json={"email": email, "password": password}
        ).status_code == 200

        event = client.post("/events", json={"title": "Dinner"})
        participant = client.post(
            f"/events/{event.json()['id']}/participants",
            json={"display_name": "Alex"},
        )
        assert participant.status_code == 201

        removed = client.delete(
            f"/events/{event.json()['id']}/participants/{participant.json()['id']}"
        )
        assert removed.status_code == 204
        assert client.get(f"/events/{event.json()['id']}/participants").json() == []


def test_only_event_owner_can_remove_participant(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-remove-owner-only.sqlite3")

    with (

        TestClient(app, headers={"Origin": "http://testserver"}) as owner,

        TestClient(app, headers={"Origin": "http://testserver"}) as other,

    ):
        for client, email, password in (
            (owner, "participant-owner@example.test", "owner password long enough"),
            (other, "participant-other@example.test", "other password long enough"),
        ):
            assert client.post(
                "/auth/register", json={"email": email, "password": password}
            ).status_code == 202
            token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
            assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
            assert client.post(
                "/auth/login", json={"email": email, "password": password}
            ).status_code == 200

        event = owner.post("/events", json={"title": "Private dinner"})
        participant = owner.post(
            f"/events/{event.json()['id']}/participants",
            json={"display_name": "Alex"},
        )
        assert participant.status_code == 201

        response = other.delete(
            f"/events/{event.json()['id']}/participants/{participant.json()['id']}"
        )
        assert response.status_code == 404
        assert owner.get(f"/events/{event.json()['id']}/participants").json()[0][
            "id"
        ] == participant.json()["id"]


def test_participant_removal_requires_authentication(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: FakeEmailSender())
    app = create_app(tmp_path / "participant-remove-auth.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.delete(
            "/events/00000000-0000-0000-0000-000000000001/participants/"
            "00000000-0000-0000-0000-000000000002"
        )
        assert response.status_code == 401



def test_each_http_request_uses_its_own_database_connection(
    tmp_path, monkeypatch
) -> None:
    app = create_app(tmp_path / "request-connections.sqlite3")
    opened_connections = []
    original_connect = http_api.connect

    def track_connection(database_path):
        connection = original_connect(database_path)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(http_api, "connect", track_connection)

    with (

        TestClient(app, headers={"Origin": "http://testserver"}) as first_client,

        TestClient(app, headers={"Origin": "http://testserver"}) as second_client,

    ):
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda client: client.get("/events"),
                    (first_client, second_client),
                )
            )

    assert [response.status_code for response in responses] == [401, 401]
    assert len(opened_connections) == 2
    assert opened_connections[0] is not opened_connections[1]


def test_unsafe_http_requests_validate_all_origin_header_variants(tmp_path) -> None:
    cases = (
        ({"Origin": "http://testserver"}, 400),
        ({"Origin": "https://testserver"}, 403),
        ({"Origin": "null"}, 403),
        ({"Origin": "http://attacker.test"}, 403),
        ({"Origin": "http://testserver/path"}, 403),
        ({"Origin": "http://testserver", "Referer": "http://attacker.test/page"}, 400),
        ({"Referer": "http://testserver/account"}, 400),
        ({"Referer": "https://attacker.test/page"}, 403),
        ({}, 400),
    )

    for index, (headers, expected_status) in enumerate(cases):
        app = create_app(tmp_path / f"origin-variant-{index}.sqlite3")
        with TestClient(app) as client:
            response = client.post(
                "/auth/verify-email",
                json={"token": "invalid-token"},
                headers=headers,
            )
        assert response.status_code == expected_status, headers



def test_outbound_email_limit_is_shared_across_auth_flows(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "smtp-recipient-http-limit.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        registration = client.post(
            "/auth/register",
            json={
                "email": "smtp-limit@example.test",
                "password": "correct horse battery staple",
            },
        )
        assert registration.status_code == 202
        first_resend = client.post(
            "/auth/verification/request",
            json={"email": "smtp-limit@example.test"},
        )
        second_resend = client.post(
            "/auth/verification/request",
            json={"email": "smtp-limit@example.test"},
        )
        blocked_resend = client.post(
            "/auth/verification/request",
            json={"email": "smtp-limit@example.test"},
        )
        blocked_reset = client.post(
            "/auth/password-reset/request",
            json={"email": "smtp-limit@example.test"},
        )
        unknown_reset = client.post(
            "/auth/password-reset/request",
            json={"email": "unknown-reset@example.test"},
        )

    assert first_resend.status_code == second_resend.status_code == 202
    assert blocked_resend.status_code == blocked_reset.status_code == 202
    assert first_resend.json() == blocked_resend.json()
    assert blocked_reset.json() == unknown_reset.json()
    assert len(sender.messages) == 3


def test_password_reset_delivery_failure_preserves_previous_token(
    tmp_path, monkeypatch
) -> None:
    class ToggleEmailSender:
        def __init__(self) -> None:
            self.fail = False
            self.messages: list[tuple[str, str, str]] = []

        def send_text(self, recipient: str, subject: str, body: str) -> None:
            if self.fail:
                raise OSError("temporary SMTP outage")
            self.messages.append((recipient, subject, body))

    sender = ToggleEmailSender()
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "reset-token-preserved.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        email = "reset-preserve@example.test"
        registration = client.post(
            "/auth/register",
            json={"email": email, "password": "correct horse battery staple"},
        )
        assert registration.status_code == 202
        verification_token = sender.messages[-1][2].split(
            "token=", maxsplit=1
        )[1].splitlines()[0]
        assert client.post(
            "/auth/verify-email", json={"token": verification_token}
        ).status_code == 200

        first_reset = client.post(
            "/auth/password-reset/request", json={"email": email}
        )
        assert first_reset.status_code == 202
        previous_reset_token = sender.messages[-1][2].split(
            "token=", maxsplit=1
        )[1].splitlines()[0]

        sender.fail = True
        failed_reset_delivery = client.post(
            "/auth/password-reset/request", json={"email": email}
        )
        assert failed_reset_delivery.status_code == 202
        assert failed_reset_delivery.json() == first_reset.json()

        reset = client.post(
            "/auth/password-reset/confirm",
            json={
                "token": previous_reset_token,
                "new_password": "a new secure password that works",
            },
        )
        assert reset.status_code == 200


def test_responses_include_baseline_security_headers(tmp_path) -> None:
    app = create_app(tmp_path / "security-headers.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        health = client.get("/health")
        landing_page = client.get("/verify-email")
        rejected = client.post("/auth/logout")

    for response in (health, landing_page, rejected):
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Permissions-Policy"] == "camera=(), microphone=()"
        assert "Strict-Transport-Security" not in response.headers
    assert landing_page.headers["Content-Security-Policy"].startswith("default-src 'none'")


def test_production_responses_include_hsts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("SMTP_STARTTLS", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_SENDER", "no-reply@example.com")
    monkeypatch.setenv("EMAIL_VERIFICATION_URL", "https://app.example.com/verify-email")
    monkeypatch.setenv("PASSWORD_RESET_URL", "https://app.example.com/reset-password")
    monkeypatch.setenv(
        "PARTICIPANT_INVITATION_URL", "https://app.example.com/accept-invitation"
    )
    app = create_app(tmp_path / "production-security-headers.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.get("/health")

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"

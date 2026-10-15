from datetime import UTC, datetime
import hashlib
import hmac

from fastapi.testclient import TestClient

from app.interfaces.web.http_api import create_app


BOT_TOKEN = "123456:example-test-token"
NOW = datetime.now(UTC)


def signed_payload(*, auth_date: int | None = None) -> dict[str, str]:
    payload = {
        "id": "123456789",
        "first_name": "Example",
        "auth_date": str(auth_date if auth_date is not None else int(NOW.timestamp())),
    }
    check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(payload.items())
    )
    secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    payload["hash"] = hmac.new(
        secret_key, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return payload


def test_valid_telegram_login_creates_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    app = create_app(tmp_path / "telegram-login.sqlite3")

    with TestClient(app) as client:
        response = client.post("/auth/telegram/login", json=signed_payload())

        assert response.status_code == 200
        assert response.json() == {"message": "Signed in with Telegram"}
        assert "gatherly_session" in response.cookies
        current_user = client.get("/auth/me")
        assert current_user.status_code == 200
        assert current_user.json()["user_id"]


def test_invalid_telegram_signature_does_not_create_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    app = create_app(tmp_path / "invalid-telegram-login.sqlite3")
    payload = signed_payload()
    payload["first_name"] = "Forged"

    with TestClient(app) as client:
        response = client.post("/auth/telegram/login", json=payload)

        assert response.status_code == 401
        assert "gatherly_session" not in response.cookies
        assert client.get("/auth/me").status_code == 401


def test_existing_telegram_identity_reuses_same_account(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    app = create_app(tmp_path / "repeat-telegram-login.sqlite3")

    with TestClient(app) as client:
        first_response = client.post("/auth/telegram/login", json=signed_payload())
        assert first_response.status_code == 200
        first_user = client.get("/auth/me").json()["user_id"]

        client.post("/auth/logout")
        second_response = client.post("/auth/telegram/login", json=signed_payload())
        assert second_response.status_code == 200
        second_user = client.get("/auth/me").json()["user_id"]

        assert second_user == first_user


def test_expired_telegram_payload_does_not_create_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    app = create_app(tmp_path / "expired-telegram-login.sqlite3")
    expired_date = int(NOW.timestamp()) - 3600

    with TestClient(app) as client:
        response = client.post(
            "/auth/telegram/login",
            json=signed_payload(auth_date=expired_date),
        )

        assert response.status_code == 401
        assert "gatherly_session" not in response.cookies
        assert client.get("/auth/me").status_code == 401


def test_missing_telegram_bot_token_rejects_login(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    app = create_app(tmp_path / "missing-telegram-token.sqlite3")

    with TestClient(app) as client:
        response = client.post("/auth/telegram/login", json=signed_payload())

        assert response.status_code == 401
        assert "gatherly_session" not in response.cookies
        assert client.get("/auth/me").status_code == 401

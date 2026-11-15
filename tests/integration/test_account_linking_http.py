from datetime import UTC, datetime
import hashlib
import hmac

from fastapi.testclient import TestClient

from app.domain.passwords import hash_password
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.connection import connect
from app.interfaces.web.constants import SESSION_COOKIE
from app.interfaces.web.http_api import create_app


def telegram_login_payload(*, bot_token: str, telegram_id: str, now: datetime) -> dict[str, str]:
    payload = {
        "id": telegram_id,
        "auth_date": str(int(now.timestamp())),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(payload.items()))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    payload["hash"] = hmac.new(
        secret_key, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return payload


def test_linking_telegram_merges_verified_account_and_rotates_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "false")
    bot_token = "test-telegram-bot-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bot_token)
    database_path = tmp_path / "account-linking-http.sqlite3"
    app = create_app(database_path)
    now = datetime.now(UTC)

    connection = connect(database_path)
    try:
        identities = AuthIdentitiesRepository(connection)
        target = identities.create_user_with_identity(
            provider="email",
            provider_subject="target@example.test",
            password_hash=hash_password("target account secure password"),
            now=now,
            verified_at=now,
        )
        source = identities.create_user_with_identity(
            provider="telegram",
            provider_subject="987654321",
            now=now,
            verified_at=now,
        )
    finally:
        connection.close()

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        login = client.post(
            "/auth/login",
            json={
                "email": "target@example.test",
                "password": "target account secure password",
            },
        )
        assert login.status_code == 200
        old_session = client.cookies.get(SESSION_COOKIE)
        assert old_session

        payload = telegram_login_payload(
            bot_token=bot_token, telegram_id="987654321", now=now
        )
        linked = client.post("/account/link/telegram", json=payload)

        assert linked.status_code == 200
        assert "linked" in linked.json()["message"].lower()
        assert client.cookies.get(SESSION_COOKIE) != old_session
        assert client.get("/auth/me").json() == {"user_id": target.user_id}

        replay = client.post("/account/link/telegram", json=payload)
        assert replay.status_code == 401

    old_client = TestClient(app, headers={"Origin": "http://testserver"})
    old_client.cookies.set(SESSION_COOKIE, old_session)
    assert old_client.get("/auth/me").status_code == 401

    connection = connect(database_path)
    try:
        linked_identity = connection.execute(
            """SELECT user_id FROM auth_identities
               WHERE provider = 'telegram' AND provider_subject = ?""",
            ("987654321",),
        ).fetchone()
        assert linked_identity["user_id"] == target.user_id
        assert connection.execute(
            "SELECT 1 FROM users WHERE id = ?", (source.user_id,)
        ).fetchone() is None
    finally:
        connection.close()


def test_linking_requires_authenticated_destination_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "false")
    database_path = tmp_path / "account-linking-recent-auth.sqlite3"
    app = create_app(database_path)

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        # An unauthenticated request must never create or merge an identity.
        response = client.post(
            "/account/link/email",
            json={"email": "source@example.test", "password": "a secure password"},
        )

    assert response.status_code == 401


def test_linking_email_merges_verified_account_and_rotates_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "false")
    bot_token = "test-telegram-bot-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bot_token)
    database_path = tmp_path / "account-linking-email-http.sqlite3"
    app = create_app(database_path)
    now = datetime.now(UTC)

    connection = connect(database_path)
    try:
        identities = AuthIdentitiesRepository(connection)
        target = identities.create_user_with_identity(
            provider="telegram",
            provider_subject="123456789",
            now=now,
            verified_at=now,
        )
        source = identities.create_user_with_identity(
            provider="email",
            provider_subject="source@example.test",
            password_hash=hash_password("source account secure password"),
            now=now,
            verified_at=now,
        )
    finally:
        connection.close()

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        payload = telegram_login_payload(
            bot_token=bot_token, telegram_id="123456789", now=now
        )
        login = client.post("/auth/telegram/login", json=payload)
        assert login.status_code == 200
        old_session = client.cookies.get(SESSION_COOKIE)
        assert old_session

        linked = client.post(
            "/account/link/email",
            json={
                "email": "source@example.test",
                "password": "source account secure password",
            },
        )

        assert linked.status_code == 200
        assert client.cookies.get(SESSION_COOKIE) != old_session
        assert client.get("/auth/me").json() == {"user_id": target.user_id}

    connection = connect(database_path)
    try:
        linked_identity = connection.execute(
            """SELECT user_id FROM auth_identities
               WHERE provider = 'email' AND provider_subject = ?""",
            ("source@example.test",),
        ).fetchone()
        assert linked_identity["user_id"] == target.user_id
        assert connection.execute(
            "SELECT 1 FROM users WHERE id = ?", (source.user_id,)
        ).fetchone() is None
    finally:
        connection.close()


def test_account_identity_list_and_telegram_widget_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@gatherly_test_bot")
    database_path = tmp_path / "account-identities-list.sqlite3"
    app = create_app(database_path)
    now = datetime.now(UTC)
    connection = connect(database_path)
    try:
        identities = AuthIdentitiesRepository(connection)
        identities.create_user_with_identity(
            provider="email",
            provider_subject="owner@example.test",
            password_hash=hash_password("owner account secure password"),
            now=now,
            verified_at=now,
        )
    finally:
        connection.close()

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        login = client.post(
            "/auth/login",
            json={
                "email": "owner@example.test",
                "password": "owner account secure password",
            },
        )
        assert login.status_code == 200
        listed = client.get("/account/identities")
        assert listed.status_code == 200
        assert listed.json() == [
            {
                "provider": "email",
                "identifier": "owner@example.test",
                "verified": True,
            }
        ]
        config = client.get("/account/link/config")
        assert config.status_code == 200
        assert config.json() == {"telegram_bot_username": "gatherly_test_bot"}

    unauthenticated = TestClient(app)
    assert unauthenticated.get("/account/identities").status_code == 401

import base64
from datetime import UTC, datetime, timedelta
import os

import pytest

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.notifications_repository import NotificationsRepository
from app.infrastructure.database.push_subscriptions_repository import (
    PushSubscriptionsRepository,
    decode_subscription_encryption_key,
)


NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
ENCRYPTION_KEY = os.urandom(32)
P256DH = base64.urlsafe_b64encode(os.urandom(65)).decode("ascii").rstrip("=")
AUTH = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii").rstrip("=")
ENDPOINT = "https://push.example.test/send/opaque-secret"


def _setup(connection):
    migrate(connection)
    connection.execute(
        "INSERT INTO users(id, created_at) VALUES (?, ?)",
        ("push-user", NOW.isoformat()),
    )
    connection.commit()
    NotificationsRepository(connection).create(
        user_id="push-user",
        event_id=None,
        kind="event_updated",
        title="Meeting updated",
        body="Your meeting has changed.",
        dedupe_key="push-meeting-updated",
        now=NOW,
    )


def test_push_subscription_is_encrypted_and_delivery_is_materialized(tmp_path) -> None:
    connection = connect(tmp_path / "push-repository.sqlite3")
    try:
        _setup(connection)
        notifications = NotificationsRepository(connection)
        notifications.set_push_enabled(
            user_id="push-user", enabled=True, now=NOW
        )
        repository = PushSubscriptionsRepository(connection)

        subscription_id = repository.upsert_subscription(
            user_id="push-user",
            endpoint=ENDPOINT,
            p256dh=P256DH,
            auth=AUTH,
            encryption_key=ENCRYPTION_KEY,
            now=NOW,
        )
        stored = connection.execute(
            "SELECT subscription_ciphertext FROM push_subscriptions WHERE id = ?",
            (subscription_id,),
        ).fetchone()
        ciphertext = bytes(stored["subscription_ciphertext"])
        assert ENDPOINT.encode() not in ciphertext
        assert P256DH.encode() not in ciphertext
        assert AUTH.encode() not in ciphertext
        assert repository.has_active_subscription(user_id="push-user")

        pending = repository.pending_deliveries(
            encryption_key=ENCRYPTION_KEY,
            now=NOW + timedelta(minutes=1),
        )
        assert len(pending) == 1
        assert pending[0]["title"] == "Meeting updated"
        assert pending[0]["subscription_info"] == {
            "endpoint": ENDPOINT,
            "keys": {"p256dh": P256DH, "auth": AUTH},
        }

        repository.record_sent(
            delivery_id=str(pending[0]["delivery_id"]),
            now=NOW + timedelta(minutes=2),
        )
        assert repository.pending_deliveries(
            encryption_key=ENCRYPTION_KEY,
            now=NOW + timedelta(minutes=3),
        ) == []
    finally:
        connection.close()


def test_upsert_is_idempotent_and_does_not_transfer_endpoint_between_users(
    tmp_path,
) -> None:
    connection = connect(tmp_path / "push-ownership.sqlite3")
    try:
        _setup(connection)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("other-user", NOW.isoformat()),
        )
        connection.commit()
        repository = PushSubscriptionsRepository(connection)
        first_id = repository.upsert_subscription(
            user_id="push-user",
            endpoint=ENDPOINT,
            p256dh=P256DH,
            auth=AUTH,
            encryption_key=ENCRYPTION_KEY,
            now=NOW,
        )
        second_id = repository.upsert_subscription(
            user_id="push-user",
            endpoint=ENDPOINT,
            p256dh=P256DH,
            auth=AUTH,
            encryption_key=ENCRYPTION_KEY,
            now=NOW + timedelta(minutes=1),
        )
        assert first_id == second_id
        with pytest.raises(ValueError, match="another account"):
            repository.upsert_subscription(
                user_id="other-user",
                endpoint=ENDPOINT,
                p256dh=P256DH,
                auth=AUTH,
                encryption_key=ENCRYPTION_KEY,
                now=NOW,
            )
    finally:
        connection.close()


def test_invalid_push_endpoint_keys_and_encryption_key_are_rejected(tmp_path) -> None:
    connection = connect(tmp_path / "push-invalid.sqlite3")
    try:
        _setup(connection)
        repository = PushSubscriptionsRepository(connection)
        with pytest.raises(ValueError, match="HTTPS"):
            repository.upsert_subscription(
                user_id="push-user",
                endpoint="http://push.example.test/send",
                p256dh=P256DH,
                auth=AUTH,
                encryption_key=ENCRYPTION_KEY,
                now=NOW,
            )
        with pytest.raises(ValueError, match="p256dh"):
            repository.upsert_subscription(
                user_id="push-user",
                endpoint=ENDPOINT,
                p256dh="bad",
                auth=AUTH,
                encryption_key=ENCRYPTION_KEY,
                now=NOW,
            )
        with pytest.raises(ValueError, match="32 bytes"):
            repository.upsert_subscription(
                user_id="push-user",
                endpoint=ENDPOINT,
                p256dh=P256DH,
                auth=AUTH,
                encryption_key=b"short",
                now=NOW,
            )
    finally:
        connection.close()


def test_expired_subscription_deletion_disables_push_consent(tmp_path) -> None:
    connection = connect(tmp_path / "push-revoke.sqlite3")
    try:
        _setup(connection)
        notifications = NotificationsRepository(connection)
        notifications.set_push_enabled(user_id="push-user", enabled=True, now=NOW)
        repository = PushSubscriptionsRepository(connection)
        subscription_id = repository.upsert_subscription(
            user_id="push-user",
            endpoint=ENDPOINT,
            p256dh=P256DH,
            auth=AUTH,
            encryption_key=ENCRYPTION_KEY,
            now=NOW,
        )

        assert repository.delete_subscription(
            subscription_id=subscription_id,
            now=NOW + timedelta(minutes=1),
        )
        preferences = notifications.get_preferences(user_id="push-user")
        assert preferences["push_enabled"] is False
        consent = connection.execute(
            """SELECT push_consent_at, push_consent_version
               FROM notification_preferences WHERE user_id = ?""",
            ("push-user",),
        ).fetchone()
        assert consent["push_consent_at"] is None
        assert consent["push_consent_version"] is None
    finally:
        connection.close()


def test_subscription_encryption_key_requires_32_decoded_bytes() -> None:
    encoded = base64.urlsafe_b64encode(ENCRYPTION_KEY).decode("ascii").rstrip("=")

    assert decode_subscription_encryption_key(encoded) == ENCRYPTION_KEY
    with pytest.raises(ValueError, match="32 bytes"):
        decode_subscription_encryption_key("c2hvcnQ")

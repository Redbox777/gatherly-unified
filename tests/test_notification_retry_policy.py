import base64
from datetime import UTC, datetime, timedelta

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.notification_retry_policy import next_retry_at
from app.infrastructure.database.notifications_repository import NotificationsRepository
from app.infrastructure.database.push_subscriptions_repository import (
    PushSubscriptionsRepository,
)


def test_retry_policy_uses_bounded_backoff_and_stops_after_five_attempts() -> None:
    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    assert next_retry_at(now=now, attempt_number=1) == now + timedelta(minutes=1)
    assert next_retry_at(now=now, attempt_number=2) == now + timedelta(minutes=5)
    assert next_retry_at(now=now, attempt_number=3) == now + timedelta(minutes=15)
    assert next_retry_at(now=now, attempt_number=4) == now + timedelta(hours=1)
    assert next_retry_at(now=now, attempt_number=5) is None


def test_email_and_telegram_retries_wait_until_scheduled_time(tmp_path) -> None:
    connection = connect(tmp_path / "channel-retries.sqlite3")
    try:
        migrate(connection)
        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)", ("user-1", now.isoformat())
        )
        connection.execute(
            """INSERT INTO auth_identities(
                id, user_id, provider, provider_subject, password_hash,
                verified_at, created_at
            ) VALUES (?, ?, 'email', ?, ?, ?, ?)""",
            ("email-1", "user-1", "person@example.test", "hash", now.isoformat(), now.isoformat()),
        )
        connection.execute(
            """INSERT INTO auth_identities(
                id, user_id, provider, provider_subject, password_hash,
                verified_at, created_at
            ) VALUES (?, ?, 'telegram', ?, NULL, ?, ?)""",
            ("telegram-1", "user-1", "123456", now.isoformat(), now.isoformat()),
        )
        connection.commit()
        repository = NotificationsRepository(connection)
        repository.set_preferences(
            user_id="user-1", values={"telegram_enabled": True}, now=now
        )
        repository.create(
            user_id="user-1",
            event_id=None,
            kind="event_updated",
            title="Update",
            body="Details",
            dedupe_key="retry-test",
            now=now,
        )

        assert len(repository.pending_email(now=now)) == 1
        assert len(repository.pending_telegram(now=now)) == 1
        repository.record_email_failure(
            notification_id=repository.pending_email(now=now)[0]["id"],
            error_name="TimeoutError",
            now=now,
        )
        repository.record_telegram_failure(
            notification_id=repository.pending_telegram(now=now)[0]["id"],
            error_name="TimeoutError",
            now=now,
        )
        assert repository.pending_email(now=now + timedelta(seconds=59)) == []
        assert repository.pending_telegram(now=now + timedelta(seconds=59)) == []
        assert len(repository.pending_email(now=now + timedelta(minutes=1))) == 1
        assert len(repository.pending_telegram(now=now + timedelta(minutes=1))) == 1

        notification_id = repository.pending_email(now=now + timedelta(minutes=1))[0]["id"]
        for _attempt in range(2, 6):
            attempt_time = now + timedelta(minutes=1)
            repository.record_email_failure(
                notification_id=notification_id,
                error_name="TimeoutError",
                now=attempt_time,
            )
        assert repository.pending_email(now=now + timedelta(days=2)) == []
        attempts = connection.execute(
            "SELECT email_attempts, email_next_attempt_at FROM notifications WHERE id = ?",
            (notification_id,),
        ).fetchone()
        assert attempts["email_attempts"] == 5
        assert attempts["email_next_attempt_at"] is None
    finally:
        connection.close()


def test_push_delivery_retries_respect_backoff_and_attempt_cap(tmp_path) -> None:
    connection = connect(tmp_path / "push-retries.sqlite3")
    try:
        migrate(connection)
        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)", ("user-1", now.isoformat())
        )
        connection.commit()
        notifications = NotificationsRepository(connection)
        notifications.create(
            user_id="user-1",
            event_id=None,
            kind="event_updated",
            title="Update",
            body="Details",
            dedupe_key="push-retry-test",
            now=now,
        )
        encryption_key = b"e" * 32
        push = PushSubscriptionsRepository(connection)
        push.upsert_subscription(
            user_id="user-1",
            endpoint="https://fcm.googleapis.com/fcm/send/test-endpoint",
            p256dh=base64.b64encode(b"\x04" + b"p" * 64).decode("ascii"),
            auth=base64.b64encode(b"a" * 16).decode("ascii"),
            encryption_key=encryption_key,
            now=now,
        )
        notifications.set_push_enabled(user_id="user-1", enabled=True, now=now)
        pending = push.pending_deliveries(
            encryption_key=encryption_key, now=now
        )
        assert len(pending) == 1
        delivery_id = str(pending[0]["delivery_id"])
        push.record_failure(
            delivery_id=delivery_id, error_name="TimeoutError", now=now
        )
        assert push.pending_deliveries(
            encryption_key=encryption_key, now=now + timedelta(seconds=59)
        ) == []
        assert len(
            push.pending_deliveries(
                encryption_key=encryption_key, now=now + timedelta(minutes=1)
            )
        ) == 1
        for _ in range(4):
            push.record_failure(
                delivery_id=delivery_id,
                error_name="TimeoutError",
                now=now + timedelta(hours=2),
            )
        assert push.pending_deliveries(
            encryption_key=encryption_key, now=now + timedelta(days=2)
        ) == []
        stored = connection.execute(
            "SELECT attempts, next_attempt_at FROM push_deliveries WHERE id = ?",
            (delivery_id,),
        ).fetchone()
        assert stored["attempts"] == 5
        assert stored["next_attempt_at"] is None
    finally:
        connection.close()

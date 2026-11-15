from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.infrastructure import notifications_worker as worker


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeBugReportsRepository:
    deleted = 0
    cutoffs: list[datetime] = []

    def __init__(self, _connection: FakeConnection) -> None:
        pass

    def delete_older_than(self, *, created_before: datetime) -> int:
        assert created_before.tzinfo is not None
        self.cutoffs.append(created_before)
        return self.deleted


class FakeNotificationsRepository:
    messages: list[dict[str, str]] = []
    created = 2
    sent_ids: list[str] = []
    failed: list[tuple[str, str]] = []
    telegram_messages: list[dict[str, str]] = []
    telegram_sent_ids: list[str] = []
    telegram_failed: list[tuple[str, str]] = []
    push_messages: list[dict[str, Any]] = []
    push_sent_ids: list[str] = []
    push_failed: list[tuple[str, str]] = []
    push_deleted_ids: list[str] = []

    def __init__(self, _connection: FakeConnection) -> None:
        pass

    def materialize_due_reminders(self, *, now: datetime) -> int:
        assert now.tzinfo is not None
        return self.created

    def pending_email(self) -> list[dict[str, str]]:
        return self.messages

    def record_email_failure(self, *, notification_id: str, error_name: str) -> None:
        self.failed.append((notification_id, error_name))

    def record_email_sent(self, *, notification_id: str, now: datetime) -> None:
        assert now.tzinfo is not None
        self.sent_ids.append(notification_id)

    def pending_telegram(self, *, limit: int = 20) -> list[dict[str, str]]:
        assert limit == 20
        return self.telegram_messages

    def record_telegram_failure(
        self, *, notification_id: str, error_name: str
    ) -> None:
        self.telegram_failed.append((notification_id, error_name))

    def record_telegram_sent(self, *, notification_id: str, now: datetime) -> None:
        assert now.tzinfo is not None
        self.telegram_sent_ids.append(notification_id)


class FakePushSubscriptionsRepository:
    def __init__(self, _connection: FakeConnection) -> None:
        pass

    def pending_deliveries(self, *, encryption_key, now, limit=100):
        assert encryption_key
        assert now.tzinfo is not None
        assert limit == 100
        return self.push_messages

    def record_failure(self, *, delivery_id: str, error_name: str, now: datetime):
        assert now.tzinfo is not None
        self.push_failed.append((delivery_id, error_name))

    def record_sent(self, *, delivery_id: str, now: datetime):
        assert now.tzinfo is not None
        self.push_sent_ids.append(delivery_id)

    def delete_subscription(self, *, subscription_id: str, now: datetime):
        assert now.tzinfo is not None
        self.push_deleted_ids.append(subscription_id)
        return True


class FakeTelegramSender:
    outcomes: dict[str, Exception | None] = {}
    sent: list[str] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def send_text(self, *, chat_id: str, text: str) -> None:
        assert text
        self.sent.append(chat_id)
        error = self.outcomes.get(chat_id)
        if error is not None:
            raise error


class FakeWebPushSender:
    outcomes: dict[str, Exception] = {}
    sent: list[tuple[str, dict[str, str]]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def send(self, *, subscription_info: dict[str, object], payload: dict[str, str]) -> None:
        endpoint = str(subscription_info["endpoint"])
        self.sent.append((endpoint, payload))
        error = self.outcomes.get(endpoint)
        if error is not None:
            raise error


class FakeRateLimitsRepository:
    retry_values: list[int | None] = []

    def __init__(self, _connection: FakeConnection) -> None:
        self.calls = 0

    def consume(self, *, recipient: str, requester: str, now: datetime) -> int | None:
        assert recipient
        assert requester == "notification-worker"
        assert now.tzinfo is not None
        value = self.retry_values[self.calls]
        self.calls += 1
        return value


class FakeSender:
    outcomes: dict[str, Exception | None] = {}
    sent: list[str] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def send_text(self, *, recipient: str, subject: str, body: str) -> None:
        assert subject
        assert body
        self.sent.append(recipient)
        error = self.outcomes.get(recipient)
        if error is not None:
            raise error


@pytest.fixture
def worker_fakes(monkeypatch: pytest.MonkeyPatch) -> FakeConnection:
    connection = FakeConnection()
    FakeBugReportsRepository.deleted = 0
    FakeBugReportsRepository.cutoffs = []
    FakeNotificationsRepository.messages = []
    FakeNotificationsRepository.sent_ids = []
    FakeNotificationsRepository.failed = []
    FakeNotificationsRepository.telegram_messages = []
    FakeNotificationsRepository.telegram_sent_ids = []
    FakeNotificationsRepository.telegram_failed = []
    FakePushSubscriptionsRepository.push_messages = []
    FakePushSubscriptionsRepository.push_sent_ids = []
    FakePushSubscriptionsRepository.push_failed = []
    FakePushSubscriptionsRepository.push_deleted_ids = []
    FakeRateLimitsRepository.retry_values = []
    FakeSender.outcomes = {}
    FakeSender.sent = []
    FakeTelegramSender.outcomes = {}
    FakeTelegramSender.sent = []
    FakeWebPushSender.outcomes = {}
    FakeWebPushSender.sent = []
    monkeypatch.setattr(worker, "connect", lambda _path: connection)
    monkeypatch.setattr(worker, "migrate", lambda _connection: None)
    monkeypatch.setattr(worker, "BugReportsRepository", FakeBugReportsRepository)
    monkeypatch.setattr(worker, "NotificationsRepository", FakeNotificationsRepository)
    monkeypatch.setattr(worker, "SmtpRateLimitsRepository", FakeRateLimitsRepository)
    monkeypatch.setattr(worker, "SmtpEmailSender", FakeSender)
    monkeypatch.setattr(worker, "TelegramBotSender", FakeTelegramSender)
    monkeypatch.setattr(
        worker, "PushSubscriptionsRepository", FakePushSubscriptionsRepository
    )
    monkeypatch.setattr(worker, "WebPushSender", FakeWebPushSender)
    monkeypatch.delenv("BUG_REPORT_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("WEB_PUSH_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("WEB_PUSH_VAPID_SUBJECT", raising=False)
    monkeypatch.delenv("WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY", raising=False)
    return connection


def test_worker_materializes_reminders_without_smtp(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_SENDER", raising=False)

    assert worker.run_once("worker.sqlite3") == (2, 0, 0, 0)
    assert len(FakeBugReportsRepository.cutoffs) == 1
    assert worker_fakes.closed


def test_worker_expires_diagnostic_reports_without_smtp(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_SENDER", raising=False)
    monkeypatch.setenv("BUG_REPORT_RETENTION_DAYS", "30")
    FakeBugReportsRepository.deleted = 4

    assert worker.run_once("worker.sqlite3") == (2, 0, 0, 0)
    assert len(FakeBugReportsRepository.cutoffs) == 1
    assert worker_fakes.closed


def test_worker_sends_pending_emails_and_records_failures(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMTP_HOST", "mail.example.test")
    monkeypatch.setenv("SMTP_SENDER", "gatherly@example.test")
    FakeNotificationsRepository.messages = [
        {"id": "limited", "email": "limited@example.test", "title": "Reminder", "body": "Body"},
        {"id": "sent", "email": "sent@example.test", "title": "Reminder", "body": "Body"},
        {"id": "failed", "email": "failed@example.test", "title": "Reminder", "body": "Body"},
    ]
    FakeRateLimitsRepository.retry_values = [30, None, None]
    FakeSender.outcomes = {"failed@example.test": TimeoutError()}

    assert worker.run_once("worker.sqlite3") == (2, 1, 0, 0)
    assert FakeSender.sent == ["sent@example.test", "failed@example.test"]
    assert FakeNotificationsRepository.sent_ids == ["sent"]
    assert FakeNotificationsRepository.failed == [("failed", "TimeoutError")]
    assert worker_fakes.closed


def test_worker_uses_database_environment_fallback(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_SENDER", raising=False)
    monkeypatch.setenv("GATHERLY_DATABASE_PATH", "from-env.sqlite3")
    connected: list[str] = []
    monkeypatch.setattr(
        worker, "connect", lambda path: connected.append(str(path)) or worker_fakes
    )

    assert worker.run_once() == (2, 0, 0, 0)
    assert connected == ["from-env.sqlite3"]


def test_worker_main_configures_logging_and_reports_counts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    configured: list[bool] = []
    monkeypatch.setattr(worker, "configure_application_logging", lambda: configured.append(True))
    monkeypatch.setattr(worker, "run_once", lambda: (3, 2, 1, 4))

    worker.main()

    assert configured == [True]
    record = next(
        item for item in caplog.records if item.message == "Notification worker completed"
    )
    assert record.notifications_created == 3
    assert record.emails_sent == 2
    assert record.telegram_messages_sent == 1
    assert record.push_messages_sent == 4


def test_worker_rejects_insecure_production_smtp_before_opening_database(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://gatherly.example")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("SMTP_SENDER", "no-reply@gatherly.example")
    monkeypatch.setenv("SMTP_STARTTLS", "false")
    monkeypatch.setenv("EMAIL_VERIFICATION_URL", "https://gatherly.example/verify-email")
    monkeypatch.setenv("PASSWORD_RESET_URL", "https://gatherly.example/reset-password")
    monkeypatch.setenv(
        "PARTICIPANT_INVITATION_URL",
        "https://gatherly.example/accept-invitation",
    )

    with pytest.raises(ValueError, match="SMTP_STARTTLS must be true"):
        worker.run_once("worker.sqlite3")

    assert not worker_fakes.closed



def test_worker_sends_opted_in_telegram_messages_without_smtp(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_SENDER", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-bot-token")
    FakeNotificationsRepository.telegram_messages = [
        {
            "id": "telegram-1",
            "chat_id": "123456",
            "title": "Встреча скоро",
            "body": "Начало через час",
        }
    ]

    assert worker.run_once("worker.sqlite3") == (2, 0, 1, 0)
    assert FakeTelegramSender.sent == ["123456"]
    assert FakeNotificationsRepository.telegram_sent_ids == ["telegram-1"]
    assert worker_fakes.closed


def test_worker_records_telegram_delivery_failures_without_leaking_details(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_SENDER", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "private-test-token")
    FakeNotificationsRepository.telegram_messages = [
        {
            "id": "telegram-failed",
            "chat_id": "987654",
            "title": "Встреча скоро",
            "body": "Начало через час",
        }
    ]
    FakeTelegramSender.outcomes = {"987654": TimeoutError("private provider detail")}

    assert worker.run_once("worker.sqlite3") == (2, 0, 0, 0)
    assert FakeNotificationsRepository.telegram_failed == [
        ("telegram-failed", "TimeoutError")
    ]
    assert "private provider detail" not in caplog.text
    assert "private-test-token" not in caplog.text
    assert worker_fakes.closed



def test_worker_sends_push_messages_when_vapid_configuration_is_present(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_SENDER", raising=False)
    monkeypatch.setenv("WEB_PUSH_VAPID_PRIVATE_KEY", "private-vapid-key")
    monkeypatch.setenv("WEB_PUSH_VAPID_SUBJECT", "mailto:ops@example.test")
    monkeypatch.setenv(
        "WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY",
        "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY",
    )
    FakePushSubscriptionsRepository.push_messages = [
        {
            "delivery_id": "push-delivery-1",
            "subscription_id": "push-subscription-1",
            "subscription_info": {
                "endpoint": "https://push.example.test/send/device"
            },
            "title": "Встреча скоро",
            "body": "Начало через час",
        }
    ]

    assert worker.run_once("worker.sqlite3") == (2, 0, 0, 1)
    assert FakeWebPushSender.sent == [
        (
            "https://push.example.test/send/device",
            {
                "title": "Gatherly",
                "body": "У вас новое уведомление. Откройте приложение.",
                "url": "/",
            },
        )
    ]
    assert FakePushSubscriptionsRepository.push_sent_ids == ["push-delivery-1"]
    assert worker_fakes.closed


def test_worker_revokes_expired_push_endpoint_without_logging_subscription_data(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    from app.infrastructure.web_push_sender import WebPushDeliveryError

    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_SENDER", raising=False)
    monkeypatch.setenv("WEB_PUSH_VAPID_PRIVATE_KEY", "private-vapid-key")
    monkeypatch.setenv("WEB_PUSH_VAPID_SUBJECT", "mailto:ops@example.test")
    monkeypatch.setenv(
        "WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY",
        "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY",
    )
    FakePushSubscriptionsRepository.push_messages = [
        {
            "delivery_id": "push-delivery-410",
            "subscription_id": "push-subscription-410",
            "subscription_info": {
                "endpoint": "https://push.example.test/private-endpoint"
            },
            "title": "Meeting",
            "body": "Reminder",
        }
    ]
    FakeWebPushSender.outcomes = {
        "https://push.example.test/private-endpoint": WebPushDeliveryError(410)
    }

    assert worker.run_once("worker.sqlite3") == (2, 0, 0, 0)
    assert FakePushSubscriptionsRepository.push_failed == [
        ("push-delivery-410", "WebPushDeliveryError")
    ]
    assert FakePushSubscriptionsRepository.push_deleted_ids == [
        "push-subscription-410"
    ]
    assert "private-endpoint" not in caplog.text
    assert "private-vapid-key" not in caplog.text
    assert worker_fakes.closed

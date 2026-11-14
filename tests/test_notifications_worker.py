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


class FakeNotificationsRepository:
    messages: list[dict[str, str]] = []
    created = 2
    sent_ids: list[str] = []
    failed: list[tuple[str, str]] = []

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
    FakeNotificationsRepository.messages = []
    FakeNotificationsRepository.sent_ids = []
    FakeNotificationsRepository.failed = []
    FakeRateLimitsRepository.retry_values = []
    FakeSender.outcomes = {}
    FakeSender.sent = []
    monkeypatch.setattr(worker, "connect", lambda _path: connection)
    monkeypatch.setattr(worker, "migrate", lambda _connection: None)
    monkeypatch.setattr(worker, "NotificationsRepository", FakeNotificationsRepository)
    monkeypatch.setattr(worker, "SmtpRateLimitsRepository", FakeRateLimitsRepository)
    monkeypatch.setattr(worker, "SmtpEmailSender", FakeSender)
    return connection


def test_worker_materializes_reminders_without_smtp(
    worker_fakes: FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_SENDER", raising=False)

    assert worker.run_once("worker.sqlite3") == (2, 0)
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

    assert worker.run_once("worker.sqlite3") == (2, 1)
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

    assert worker.run_once() == (2, 0)
    assert connected == ["from-env.sqlite3"]


def test_worker_main_configures_logging_and_reports_counts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    configured: list[bool] = []
    monkeypatch.setattr(worker, "configure_application_logging", lambda: configured.append(True))
    monkeypatch.setattr(worker, "run_once", lambda: (3, 2))

    worker.main()

    assert configured == [True]
    assert "Notification worker completed" in caplog.text
    assert "notifications_created" in caplog.text

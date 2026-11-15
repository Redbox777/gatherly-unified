from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.smtp_rate_limits_repository import (
    SmtpRateLimitsRepository,
)


NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def test_recipient_limit_is_normalized_shared_and_expires(tmp_path) -> None:
    connection = connect(tmp_path / "smtp-recipient-limit.sqlite3")
    migrate(connection)
    repository = SmtpRateLimitsRepository(connection)

    for index in range(3):
        assert repository.consume(
            recipient=" Person@Example.Test ",
            requester=f"192.0.2.{index}",
            now=NOW,
        ) is None

    blocked = repository.consume(
        recipient="person@example.test",
        requester="192.0.2.10",
        now=NOW,
    )
    assert blocked == 900

    assert repository.consume(
        recipient="person@example.test",
        requester="192.0.2.10",
        now=NOW + timedelta(minutes=15),
    ) is None
    connection.close()


def test_requester_limit_is_shared_across_recipients(tmp_path) -> None:
    connection = connect(tmp_path / "smtp-requester-limit.sqlite3")
    migrate(connection)
    repository = SmtpRateLimitsRepository(connection)

    for index in range(5):
        assert repository.consume(
            recipient=f"person-{index}@example.test",
            requester="203.0.113.10",
            now=NOW,
        ) is None

    assert repository.consume(
        recipient="sixth@example.test",
        requester="203.0.113.10",
        now=NOW,
    ) == 60
    assert repository.consume(
        recipient="sixth@example.test",
        requester="203.0.113.10",
        now=NOW + timedelta(minutes=1),
    ) is None
    connection.close()


def test_recipient_quota_is_atomic_across_independent_connections(tmp_path) -> None:
    database_path = tmp_path / "smtp-concurrent-limit.sqlite3"
    setup = connect(database_path)
    migrate(setup)
    setup.close()

    def attempt(index: int) -> int | None:
        connection = connect(database_path)
        try:
            return SmtpRateLimitsRepository(connection).consume(
                recipient="shared@example.test",
                requester=f"198.51.100.{index}",
                now=NOW,
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))

    assert results.count(None) == 3
    assert results.count(900) == 5


def test_rate_limiter_rejects_naive_datetime(tmp_path) -> None:
    connection = connect(tmp_path / "smtp-naive-time.sqlite3")
    migrate(connection)
    repository = SmtpRateLimitsRepository(connection)

    try:
        repository.consume(
            recipient="person@example.test",
            requester="192.0.2.1",
            now=datetime(2026, 11, 13, 12, 0),
        )
    except ValueError as error:
        assert "timezone-aware" in str(error)
    else:
        raise AssertionError("naive datetimes must be rejected")
    finally:
        connection.close()


def test_repository_uses_injected_quota_configuration(tmp_path) -> None:
    connection = connect(tmp_path / "smtp-custom-limit.sqlite3")
    migrate(connection)
    repository = SmtpRateLimitsRepository(
        connection,
        recipient_limit=2,
        recipient_window_seconds=30,
        requester_limit=10,
        requester_window_seconds=120,
    )

    for _ in range(2):
        assert repository.consume(
            recipient="custom@example.test",
            requester="192.0.2.1",
            now=NOW,
        ) is None

    assert repository.consume(
        recipient="custom@example.test",
        requester="192.0.2.1",
        now=NOW,
    ) == 30
    connection.close()

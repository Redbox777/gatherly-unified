from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.http_rate_limits_repository import (
    HttpRateLimitsRepository,
)


NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def test_http_limit_is_shared_across_connections_and_expires(tmp_path) -> None:
    database_path = tmp_path / "http-rate-limit.sqlite3"
    setup = connect(database_path)
    migrate(setup)
    setup.close()

    for _ in range(5):
        connection = connect(database_path)
        try:
            assert HttpRateLimitsRepository(connection).consume(
                requester="203.0.113.10",
                route="/auth/login",
                now=NOW,
            ) is None
        finally:
            connection.close()

    connection = connect(database_path)
    try:
        repository = HttpRateLimitsRepository(connection)
        assert repository.consume(
            requester="203.0.113.10",
            route="/auth/login",
            now=NOW,
        ) == 60
        assert repository.consume(
            requester="203.0.113.10",
            route="/auth/login",
            now=NOW + timedelta(minutes=1),
        ) is None
    finally:
        connection.close()


def test_http_limit_is_atomic_across_independent_connections(tmp_path) -> None:
    database_path = tmp_path / "http-concurrent-limit.sqlite3"
    setup = connect(database_path)
    migrate(setup)
    setup.close()

    def attempt(_: int) -> int | None:
        connection = connect(database_path)
        try:
            return HttpRateLimitsRepository(connection).consume(
                requester="198.51.100.20",
                route="/auth/login",
                now=NOW,
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(attempt, range(10)))

    assert results.count(None) == 5
    assert results.count(60) == 5


def test_http_limit_scopes_counters_by_route_and_requester(tmp_path) -> None:
    connection = connect(tmp_path / "http-scope.sqlite3")
    migrate(connection)
    repository = HttpRateLimitsRepository(connection)
    try:
        for _ in range(5):
            assert repository.consume(
                requester="203.0.113.10",
                route="/auth/login",
                now=NOW,
            ) is None
        assert repository.consume(
            requester="203.0.113.11",
            route="/auth/login",
            now=NOW,
        ) is None
        assert repository.consume(
            requester="203.0.113.10",
            route="/auth/register",
            now=NOW,
        ) is None
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("requester", "route", "limit", "window_seconds"),
    [
        ("", "/auth/login", 5, 60),
        ("127.0.0.1", "auth/login", 5, 60),
        ("127.0.0.1", "/auth/login", 0, 60),
        ("127.0.0.1", "/auth/login", 5, 0),
    ],
)
def test_http_limit_rejects_invalid_scope_or_policy(
    tmp_path, requester: str, route: str, limit: int, window_seconds: int
) -> None:
    connection = connect(tmp_path / "http-invalid.sqlite3")
    migrate(connection)
    try:
        with pytest.raises(ValueError):
            HttpRateLimitsRepository(connection).consume(
                requester=requester,
                route=route,
                now=NOW,
                limit=limit,
                window_seconds=window_seconds,
            )
    finally:
        connection.close()


def test_http_limit_rejects_naive_datetime(tmp_path) -> None:
    connection = connect(tmp_path / "http-naive.sqlite3")
    migrate(connection)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            HttpRateLimitsRepository(connection).consume(
                requester="127.0.0.1",
                route="/auth/login",
                now=datetime(2026, 11, 13, 12, 0),
            )
    finally:
        connection.close()

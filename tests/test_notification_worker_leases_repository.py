from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.notification_worker_leases_repository import (
    NotificationWorkerLeaseRepository,
)


def test_worker_lease_excludes_a_second_active_worker_and_allows_expired_takeover(tmp_path) -> None:
    database_path = tmp_path / "worker-lease.sqlite3"
    connection = connect(database_path)
    try:
        migrate(connection)
        first = NotificationWorkerLeaseRepository(connection)
        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        duration = timedelta(minutes=5)

        assert first.acquire(
            worker_name="notifications",
            owner_token="worker-one",
            now=now,
            lease_duration=duration,
        )
        assert not first.acquire(
            worker_name="notifications",
            owner_token="worker-two",
            now=now + timedelta(seconds=1),
            lease_duration=duration,
        )
        assert first.acquire(
            worker_name="notifications",
            owner_token="worker-two",
            now=now + duration + timedelta(seconds=1),
            lease_duration=duration,
        )
        assert not first.renew(
            worker_name="notifications",
            owner_token="worker-one",
            now=now + duration + timedelta(seconds=2),
            lease_duration=duration,
        )
        assert not first.release(
            worker_name="notifications", owner_token="worker-one"
        )
        assert first.renew(
            worker_name="notifications",
            owner_token="worker-two",
            now=now + duration + timedelta(seconds=2),
            lease_duration=duration,
        )
        assert first.release(
            worker_name="notifications", owner_token="worker-two"
        )
    finally:
        connection.close()


def test_only_one_concurrent_worker_can_acquire_lease(tmp_path) -> None:
    database_path = tmp_path / "worker-lease-race.sqlite3"
    setup = connect(database_path)
    try:
        migrate(setup)
    finally:
        setup.close()
    barrier = Barrier(2)
    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

    def acquire(token: str) -> bool:
        connection = connect(database_path)
        try:
            barrier.wait(timeout=5)
            repository = NotificationWorkerLeaseRepository(connection)
            return repository.acquire(
                worker_name="notifications",
                owner_token=token,
                now=now,
                lease_duration=timedelta(minutes=5),
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, ("worker-one", "worker-two")))

    assert sorted(results) == [False, True]


def test_worker_lease_rejects_invalid_expiry_inputs(tmp_path) -> None:
    connection = connect(tmp_path / "worker-lease-invalid.sqlite3")
    try:
        migrate(connection)
        repository = NotificationWorkerLeaseRepository(connection)
        with pytest.raises(ValueError, match="timezone-aware"):
            repository.acquire(
                worker_name="notifications",
                owner_token="worker-one",
                now=datetime(2026, 11, 15, 12, 0),
                lease_duration=timedelta(minutes=5),
            )
        with pytest.raises(ValueError, match="positive"):
            repository.acquire(
                worker_name="notifications",
                owner_token="worker-one",
                now=datetime(2026, 11, 15, 12, 0, tzinfo=UTC),
                lease_duration=timedelta(0),
            )
    finally:
        connection.close()

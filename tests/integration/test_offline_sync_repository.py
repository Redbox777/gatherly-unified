"""Atomic offline command processing and idempotency tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.offline_sync_repository import (
    OfflineSyncError,
    OfflineSyncRepository,
)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "offline-sync.sqlite3"
    connection = connect(path)
    migrate(connection)
    now = datetime.now(UTC).isoformat()
    connection.execute("INSERT INTO users(id, created_at) VALUES ('owner', ?)", (now,))
    connection.execute("INSERT INTO users(id, created_at) VALUES ('guest', ?)", (now,))
    connection.execute(
        "INSERT INTO events(id, owner_id, title, created_at) VALUES (?, ?, ?, ?)",
        ("11111111-1111-4111-8111-111111111111", "owner", "Planning", now),
    )
    connection.execute(
        "INSERT INTO event_participants(id, event_id, display_name, added_at, linked_user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("22222222-2222-4222-8222-222222222222",
         "11111111-1111-4111-8111-111111111111", "Guest", now, "guest"),
    )
    connection.commit()
    yield path, connection
    connection.close()


def _command(
    repository: OfflineSyncRepository,
    *,
    user: str = "owner",
    operation_id: str | None = None,
    revision: int = 2,
    kind: str = "note.create",
    payload: dict[str, object] | None = None,
):
    return repository.apply(
        authenticated_user_id=user,
        operation_id=operation_id or str(uuid4()),
        event_id="11111111-1111-4111-8111-111111111111",
        base_revision=revision,
        kind=kind,
        payload=payload or {"content": "Offline note", "visibility": "shared"},
        now=datetime(2026, 11, 15, tzinfo=UTC),
    )


def test_same_operation_and_payload_replays_stored_result_once(database) -> None:
    _, connection = database
    repository = OfflineSyncRepository(connection)
    operation_id = str(uuid4())
    first = _command(repository, operation_id=operation_id)
    replay = _command(repository, operation_id=operation_id)

    assert first == replay
    assert first[0] == 201
    assert connection.execute("SELECT COUNT(*) FROM event_notes").fetchone()[0] == 1
    assert connection.execute(
        "SELECT revision FROM event_sync_state WHERE event_id = ?",
        ("11111111-1111-4111-8111-111111111111",),
    ).fetchone()[0] == 3


def test_reusing_operation_id_with_different_payload_is_rejected(database) -> None:
    _, connection = database
    repository = OfflineSyncRepository(connection)
    operation_id = str(uuid4())
    _command(repository, operation_id=operation_id)
    with pytest.raises(OfflineSyncError, match="another payload") as error:
        _command(
            repository,
            operation_id=operation_id,
            payload={"content": "Different note", "visibility": "shared"},
        )
    assert error.value.status_code == 409
    assert connection.execute("SELECT COUNT(*) FROM event_notes").fetchone()[0] == 1


def test_stale_revision_is_rejected_without_mutation(database) -> None:
    _, connection = database
    repository = OfflineSyncRepository(connection)
    _command(repository)
    with pytest.raises(OfflineSyncError, match="revision conflict") as error:
        _command(
            repository,
            revision=2,
            kind="task.create",
            payload={"title": "Do work"},
        )
    assert error.value.status_code == 409
    assert error.value.current_revision == 3
    assert connection.execute("SELECT COUNT(*) FROM event_tasks").fetchone()[0] == 0


def test_note_member_can_create_but_nonmember_and_nonowner_cannot_create_tasks(database) -> None:
    _, connection = database
    repository = OfflineSyncRepository(connection)
    result = _command(repository, user="guest")
    assert result[0] == 201

    with pytest.raises(OfflineSyncError) as error:
        _command(
            repository,
            user="guest",
            revision=3,
            kind="task.create",
            payload={"title": "Owner only"},
        )
    assert error.value.status_code == 404

    with pytest.raises(OfflineSyncError) as error:
        _command(
            repository,
            user="intruder",
            revision=3,
            payload={"content": "No access"},
        )
    assert error.value.status_code == 404


def test_task_assignee_must_belong_to_same_event(database) -> None:
    _, connection = database
    repository = OfflineSyncRepository(connection)
    with pytest.raises(OfflineSyncError, match="Assignee must belong") as error:
        _command(
            repository,
            kind="task.create",
            payload={
                "title": "Task",
                "assignee_participant_id": str(uuid4()),
            },
        )
    assert error.value.status_code == 422
    assert connection.execute("SELECT COUNT(*) FROM event_tasks").fetchone()[0] == 0


def test_task_and_venue_commands_use_owner_rules_and_increment_revision(database) -> None:
    _, connection = database
    repository = OfflineSyncRepository(connection)
    task_status, task = _command(
        repository, kind="task.create", payload={"title": "Prepare", "description": ""}
    )
    assert task_status == 201
    assert task["revision"] == 3
    task_id = str(task["result"]["id"])
    complete_status, completed = _command(
        repository,
        revision=3,
        kind="task.complete",
        payload={"task_id": task_id, "completed": True},
    )
    assert complete_status == 200
    assert completed["result"]["completed"] is True
    assert completed["revision"] == 4

    venue_status, venue = _command(
        repository,
        revision=4,
        kind="venue.set",
        payload={"venue_name": "Workshop", "address": "Main Street"},
    )
    assert venue_status == 200
    assert venue["revision"] == 5


def test_invalid_coordinates_and_unknown_fields_are_rejected(database) -> None:
    _, connection = database
    repository = OfflineSyncRepository(connection)
    with pytest.raises(OfflineSyncError, match="Latitude") as error:
        _command(
            repository,
            kind="venue.set",
            payload={"venue_name": "Hall", "address": "Road", "latitude": 91,
                     "longitude": 10},
        )
    assert error.value.status_code == 422
    with pytest.raises(OfflineSyncError, match="payload fields"):
        _command(repository, payload={"content": "x", "visibility": "private", "user_id": "owner"})


def test_failed_idempotency_receipt_rolls_back_domain_write(database) -> None:
    _, connection = database
    connection.execute(
        """CREATE TRIGGER reject_sync_receipt BEFORE INSERT ON offline_sync_idempotency
           BEGIN SELECT RAISE(ABORT, 'forced receipt failure'); END"""
    )
    with pytest.raises(Exception, match="forced receipt failure"):
        _command(OfflineSyncRepository(connection))
    assert connection.execute("SELECT COUNT(*) FROM event_notes").fetchone()[0] == 0
    assert connection.execute(
        "SELECT revision FROM event_sync_state WHERE event_id = ?",
        ("11111111-1111-4111-8111-111111111111",),
     ).fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM offline_sync_idempotency"
    ).fetchone()[0] == 0


def test_concurrent_duplicate_requests_apply_once(tmp_path) -> None:
    path = tmp_path / "concurrent-sync.sqlite3"
    setup = connect(path)
    migrate(setup)
    now = datetime.now(UTC).isoformat()
    setup.execute("INSERT INTO users(id, created_at) VALUES ('owner', ?)", (now,))
    setup.execute(
        "INSERT INTO events(id, owner_id, title, created_at) VALUES (?, ?, ?, ?)",
        ("11111111-1111-4111-8111-111111111111", "owner", "Planning", now),
    )
    setup.commit()
    setup.close()
    operation_id = str(uuid4())

    def submit():
        connection = connect(path)
        try:
            return _command(
                OfflineSyncRepository(connection), operation_id=operation_id, revision=1
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: submit(), range(2)))
    assert first == second
    verify = connect(path)
    try:
        assert verify.execute("SELECT COUNT(*) FROM event_notes").fetchone()[0] == 1
        assert verify.execute(
            "SELECT COUNT(*) FROM offline_sync_idempotency"
        ).fetchone()[0] == 1
    finally:
        verify.close()

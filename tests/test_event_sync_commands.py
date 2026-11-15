"""Atomic server-side event sync command tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest

from app.domain.auth_tokens import hash_token
from app.infrastructure.database.connection import connect, migrate
from app.application.event_sync_commands import EventSyncCommandHandler


NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
EVENT_ID = "10000000-0000-4000-8000-000000000001"
OPERATION_ID = "20000000-0000-4000-8000-000000000001"
SESSION_TOKEN = "gatherly-test-session-token"


@pytest.fixture
def sync_db(tmp_path):
    path = tmp_path / "event-sync.sqlite3"
    connection = connect(path)
    migrate(connection)
    now = NOW.isoformat()
    connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("owner", now))
    connection.execute(
        "INSERT INTO auth_sessions(id, user_id, token_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            str(uuid4()), "owner", hash_token(SESSION_TOKEN),
            (NOW - timedelta(days=1)).isoformat(),
            (NOW + timedelta(days=5)).isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) "
        "VALUES (?, ?, ?, NULL, ?)",
        (EVENT_ID, "owner", "Planning", now),
    )
    connection.commit()
    yield path, connection
    connection.close()


def _run(
    connection,
    *,
    operation_id=OPERATION_ID,
    token=SESSION_TOKEN,
    event_id=EVENT_ID,
    base_revision=0,
    kind="note.create",
    payload=None,
):
    return EventSyncCommandHandler(connection).execute(
        raw_session_token=token,
        operation_id=operation_id,
        event_id=event_id,
        base_revision=base_revision,
        kind=kind,
        payload=payload or {"content": "Remember the keys", "visibility": "private"},
        now=NOW,
    )


def _revision(connection) -> int:
    return int(
        connection.execute(
            "SELECT revision FROM event_sync_state WHERE event_id = ?", (EVENT_ID,)
        ).fetchone()["revision"]
    )


def test_same_operation_replays_identical_result_and_applies_once(sync_db):
    _, connection = sync_db
    first = _run(connection)
    replay = _run(connection)
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.replayed is True
    assert replay.body == first.body
    assert connection.execute(
        "SELECT COUNT(*) AS count FROM event_notes WHERE event_id = ?", (EVENT_ID,)
    ).fetchone()["count"] == 1
    assert _revision(connection) == 1
    assert connection.execute(
        "SELECT COUNT(*) AS count FROM sync_idempotency_records"
    ).fetchone()["count"] == 1


def test_reusing_operation_id_with_different_payload_is_rejected(sync_db):
    _, connection = sync_db
    first = _run(connection)
    second = _run(
        connection,
        payload={"content": "A different note", "visibility": "private"},
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.body["error"] == "idempotency_key_reused"
    assert connection.execute(
        "SELECT COUNT(*) AS count FROM event_notes WHERE event_id = ?", (EVENT_ID,)
    ).fetchone()["count"] == 1
    assert _revision(connection) == 1


def test_stale_revision_rejects_mutation(sync_db):
    _, connection = sync_db
    first = _run(connection)
    stale = _run(
        connection,
        operation_id="20000000-0000-4000-8000-000000000002",
        base_revision=0,
        payload={"content": "Stale note", "visibility": "private"},
    )
    assert first.status_code == 201
    assert stale.status_code == 409
    assert stale.body == {"error": "revision_conflict", "current_revision": 1}
    assert connection.execute(
        "SELECT COUNT(*) AS count FROM event_notes WHERE event_id = ?", (EVENT_ID,)
    ).fetchone()["count"] == 1


def test_domain_write_and_idempotency_record_roll_back_together(sync_db, monkeypatch):
    _, connection = sync_db
    handler = EventSyncCommandHandler(connection)
    original = handler._apply_command

    def fail_after_domain_write(**kwargs):
        original(**kwargs)
        raise RuntimeError("simulated failure before ledger insert")

    monkeypatch.setattr(handler, "_apply_command", fail_after_domain_write)
    with pytest.raises(RuntimeError, match="simulated failure"):
        handler.execute(
            raw_session_token=SESSION_TOKEN,
            operation_id=OPERATION_ID,
            event_id=EVENT_ID,
            base_revision=0,
            kind="note.create",
            payload={"content": "Atomic note", "visibility": "private"},
            now=NOW,
        )
    assert connection.execute("SELECT COUNT(*) FROM event_notes").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM sync_idempotency_records"
    ).fetchone()[0] == 0
    assert _revision(connection) == 0


def test_two_concurrent_retries_create_only_one_note(sync_db):
    path, _ = sync_db
    barrier = Barrier(2)

    def submit():
        connection = connect(path)
        try:
            barrier.wait(timeout=5)
            return _run(connection)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert [result.status_code for result in results] == [201, 201]
    assert sorted(result.replayed for result in results) == [False, True]
    check = connect(path)
    try:
        assert check.execute("SELECT COUNT(*) FROM event_notes").fetchone()[0] == 1
        assert check.execute(
            "SELECT COUNT(*) FROM sync_idempotency_records"
        ).fetchone()[0] == 1
        assert _revision(check) == 1
    finally:
        check.close()


def test_only_current_linked_member_can_create_notes_and_tasks_are_owner_only(sync_db):
    _, connection = sync_db
    now = NOW.isoformat()
    connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("member", now))
    connection.execute(
        "INSERT INTO auth_sessions(id, user_id, token_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            str(uuid4()), "member", hash_token("member-session"),
            now, (NOW + timedelta(days=1)).isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO event_participants(id, event_id, display_name, added_at, linked_user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid4()), EVENT_ID, "Member", now, "member"),
    )
    connection.commit()

    member_note = _run(
        connection,
        token="member-session",
        payload={"content": "Member note", "visibility": "private"},
    )
    member_task = _run(
        connection,
        token="member-session",
        operation_id="20000000-0000-4000-8000-000000000003",
        kind="task.create",
        base_revision=_revision(connection),
        payload={
            "title": "Restricted task",
            "description": "",
            "due_at": None,
            "assignee_participant_id": None,
        },
    )
    assert member_note.status_code == 201
    assert member_task.status_code == 404


def test_removed_member_and_cross_account_cannot_access_event(sync_db):
    _, connection = sync_db
    now = NOW.isoformat()
    connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("member", now))
    connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("outsider", now))
    connection.execute(
        "INSERT INTO auth_sessions(id, user_id, token_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            str(uuid4()), "member", hash_token("member-session"),
            now, (NOW + timedelta(days=1)).isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO auth_sessions(id, user_id, token_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            str(uuid4()), "outsider", hash_token("outsider-session"),
            now, (NOW + timedelta(days=1)).isoformat(),
        ),
    )
    participant_id = str(uuid4())
    connection.execute(
        "INSERT INTO event_participants(id, event_id, display_name, added_at, linked_user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (participant_id, EVENT_ID, "Member", now, "member"),
    )
    connection.commit()
    allowed = _run(
        connection,
        token="member-session",
        payload={"content": "Before removal", "visibility": "private"},
    )
    assert allowed.status_code == 201
    connection.execute("DELETE FROM event_participants WHERE id = ?", (participant_id,))
    connection.commit()
    denied = _run(
        connection,
        token="member-session",
        operation_id="20000000-0000-4000-8000-000000000004",
        base_revision=_revision(connection),
    )
    outsider = _run(
        connection,
        token="outsider-session",
        operation_id="20000000-0000-4000-8000-000000000005",
        base_revision=_revision(connection),
    )
    assert denied.status_code == 404
    assert outsider.status_code == 404


def test_expired_or_revoked_session_cannot_replay(sync_db):
    _, connection = sync_db
    first = _run(connection)
    assert first.status_code == 201
    connection.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ?",
        (NOW.isoformat(), hash_token(SESSION_TOKEN)),
    )
    connection.commit()
    replay = _run(connection)
    assert replay.status_code == 401


def test_invalid_assignee_is_rejected_without_mutation(sync_db):
    _, connection = sync_db
    result = _run(
        connection,
        kind="task.create",
        payload={
            "title": "Task",
            "description": "",
            "due_at": None,
            "assignee_participant_id": str(uuid4()),
        },
    )
    assert result.status_code == 422
    assert connection.execute("SELECT COUNT(*) FROM event_tasks").fetchone()[0] == 0
    assert _revision(connection) == 0

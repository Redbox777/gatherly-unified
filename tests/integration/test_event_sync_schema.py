"""Schema and trigger coverage for safe event synchronization."""

from datetime import UTC, datetime

from app.infrastructure.database.connection import (
    MIGRATIONS,
    _execute_migration,
    connect,
    migrate,
    transaction,
)


def _revision(connection, event_id: str) -> int:
    row = connection.execute(
        "SELECT revision FROM event_sync_state WHERE event_id = ?", (event_id,)
    ).fetchone()
    assert row is not None
    return int(row["revision"])


def test_sync_migration_backfills_events_and_tracks_all_allowed_domain_writes(tmp_path):
    connection = connect(tmp_path / "sync-schema.sqlite3")
    try:
        # Apply only migrations 1–23, then seed a legacy event before migration 24.
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.commit()
        for version, script in MIGRATIONS[:-1]:
            with transaction(connection):
                _execute_migration(connection, script)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, "2026-11-14T00:00:00Z"),
                )
        now = datetime.now(UTC).isoformat()
        connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("owner", now))
        connection.execute(
            "INSERT INTO events(id, owner_id, title, starts_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("event-1", "owner", "Planning", None, now),
        )
        connection.commit()
        migrate(connection)
        assert _revision(connection, "event-1") == 0

        connection.execute(
            "INSERT INTO event_participants(id, event_id, display_name, added_at) "
            "VALUES (?, ?, ?, ?)",
            ("participant-1", "event-1", "Alex", now),
        )
        assert _revision(connection, "event-1") == 1
        connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("member", now))
        connection.execute(
            "UPDATE event_participants SET display_name = ? WHERE id = ?",
            ("Alexandra", "participant-1"),
        )
        assert _revision(connection, "event-1") == 2
        connection.execute(
            "UPDATE event_participants SET linked_user_id = ? WHERE id = ?",
            ("member", "participant-1"),
        )
        assert _revision(connection, "event-1") == 3
        connection.execute("DELETE FROM event_participants WHERE id = ?", ("participant-1",))
        assert _revision(connection, "event-1") == 4

        connection.execute(
            "INSERT INTO event_notes(id, event_id, author_id, content, visibility, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("note-1", "event-1", "owner", "First", "private", now, now),
        )
        assert _revision(connection, "event-1") == 5
        connection.execute(
            "UPDATE event_notes SET content = ? WHERE id = ?", ("Changed", "note-1")
        )
        assert _revision(connection, "event-1") == 6
        connection.execute("DELETE FROM event_notes WHERE id = ?", ("note-1",))
        assert _revision(connection, "event-1") == 7

        connection.execute(
            "INSERT INTO event_tasks(id, event_id, title, description, completed, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
            ("task-1", "event-1", "Prepare", "", now, now),
        )
        assert _revision(connection, "event-1") == 8
        connection.execute(
            "UPDATE event_tasks SET completed = 1 WHERE id = ?", ("task-1",)
        )
        assert _revision(connection, "event-1") == 9
        connection.execute("DELETE FROM event_tasks WHERE id = ?", ("task-1",))
        assert _revision(connection, "event-1") == 10

        connection.execute(
            "INSERT INTO event_venues(event_id, venue_name, address) VALUES (?, ?, ?)",
            ("event-1", "Park", "Main street"),
        )
        assert _revision(connection, "event-1") == 11
        connection.execute(
            "UPDATE event_venues SET venue_name = ? WHERE event_id = ?",
            ("Town Hall", "event-1"),
        )
        assert _revision(connection, "event-1") == 12
        connection.execute("DELETE FROM event_venues WHERE event_id = ?", ("event-1",))
        assert _revision(connection, "event-1") == 13

        connection.execute(
            "UPDATE events SET title = ? WHERE id = ?", ("Updated", "event-1")
        )
        assert _revision(connection, "event-1") == 14
        connection.commit()
    finally:
        connection.close()


def test_sync_idempotency_ledger_is_user_scoped_and_cascades_on_account_delete(tmp_path):
    connection = connect(tmp_path / "sync-ledger.sqlite3")
    try:
        migrate(connection)
        now = datetime.now(UTC).isoformat()
        connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("user-1", now))
        connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("user-2", now))
        values = (
            "user-1", "12345678-1234-1234-1234-123456789abc", "event-1", "note.create",
            "a" * 64, 200, '{"ok":true}', now,
        )
        connection.execute(
            "INSERT INTO sync_idempotency_records("
            "user_id, operation_id, event_id, command_kind, payload_hash, response_status, "
            "response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        connection.commit()
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM sync_idempotency_records "
            "WHERE user_id = ? AND operation_id = ?",
            (values[0], values[1]),
        ).fetchone()["count"] == 1

        import pytest
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO sync_idempotency_records("
                "user_id, operation_id, event_id, command_kind, payload_hash, response_status, "
                "response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
        connection.rollback()
        connection.execute("DELETE FROM users WHERE id = ?", ("user-1",))
        connection.commit()
        assert connection.execute(
            "SELECT 1 FROM sync_idempotency_records WHERE user_id = ?", ("user-1",)
        ).fetchone() is None
    finally:
        connection.close()

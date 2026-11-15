"""Migration 24 revision and idempotency schema coverage."""

from app.infrastructure.database.connection import connect, migrate


def _revision(connection, event_id: str = "event-1") -> int:
    row = connection.execute(
        "SELECT revision FROM event_sync_state WHERE event_id = ?", (event_id,)
    ).fetchone()
    assert row is not None
    return int(row["revision"])


def test_migration_24_tracks_every_phase_one_online_mutation(tmp_path) -> None:
    connection = connect(tmp_path / "sync-revisions.sqlite3")
    try:
        migrate(connection)
        connection.execute("INSERT INTO users(id, created_at) VALUES ('owner', 'now')")
        connection.execute(
            "INSERT INTO events(id, owner_id, title, created_at) "
            "VALUES ('event-1', 'owner', 'Planning', 'now')"
        )
        assert _revision(connection) == 1

        connection.execute(
            "UPDATE events SET title = 'Updated' WHERE id = 'event-1'"
        )
        assert _revision(connection) == 2

        connection.execute(
            "INSERT INTO event_participants(id, event_id, display_name, added_at) "
            "VALUES ('p-1', 'event-1', 'Guest', 'now')"
        )
        assert _revision(connection) == 3
        connection.execute(
            "UPDATE event_participants SET display_name = 'Guest 2' WHERE id = 'p-1'"
        )
        assert _revision(connection) == 4

        connection.execute(
            "INSERT INTO event_notes(id, event_id, author_id, content, visibility, "
            "created_at, updated_at) VALUES "
            "('n-1', 'event-1', 'owner', 'note', 'private', 'now', 'now')"
        )
        assert _revision(connection) == 5
        connection.execute(
            "UPDATE event_notes SET content = 'changed' WHERE id = 'n-1'"
        )
        assert _revision(connection) == 6

        connection.execute(
            "INSERT INTO event_tasks(id, event_id, title, created_at, updated_at) "
            "VALUES ('t-1', 'event-1', 'Task', 'now', 'now')"
        )
        assert _revision(connection) == 7
        connection.execute(
            "UPDATE event_tasks SET completed = 1 WHERE id = 't-1'"
        )
        assert _revision(connection) == 8

        connection.execute(
            "INSERT INTO event_venues(event_id, venue_name, address) "
            "VALUES ('event-1', 'Hall', 'Main street')"
        )
        assert _revision(connection) == 9
        connection.execute(
            "UPDATE event_venues SET venue_name = 'New hall' WHERE event_id = 'event-1'"
        )
        assert _revision(connection) == 10

        connection.execute("DELETE FROM event_notes WHERE id = 'n-1'")
        assert _revision(connection) == 11
        connection.execute("DELETE FROM event_tasks WHERE id = 't-1'")
        assert _revision(connection) == 12
        connection.execute("DELETE FROM event_venues WHERE event_id = 'event-1'")
        assert _revision(connection) == 13
        connection.execute("DELETE FROM event_participants WHERE id = 'p-1'")
        assert _revision(connection) == 14
    finally:
        connection.close()


def test_idempotency_ledger_is_scoped_by_user_and_operation(tmp_path) -> None:
    connection = connect(tmp_path / "sync-idempotency.sqlite3")
    try:
        migrate(connection)
        connection.execute("INSERT INTO users(id, created_at) VALUES ('owner', 'now')")
        connection.execute(
            "INSERT INTO events(id, owner_id, title, created_at) "
            "VALUES ('event-1', 'owner', 'Planning', 'now')"
        )
        connection.execute(
            "INSERT INTO offline_sync_idempotency("
            "user_id, operation_id, event_id, payload_hash, response_status, "
            "response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("owner", "op-1", "event-1", "a" * 64, 200, '{"ok":true}', "now"),
        )
        connection.commit()
        try:
            connection.execute(
                "INSERT INTO offline_sync_idempotency("
                "user_id, operation_id, event_id, payload_hash, response_status, "
                "response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("owner", "op-1", "event-1", "b" * 64, 200, '{"ok":true}', "now"),
            )
            raise AssertionError("duplicate idempotency key was accepted")
        except Exception as error:
            assert "UNIQUE" in str(error)
    finally:
        connection.close()

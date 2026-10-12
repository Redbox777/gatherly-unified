import sqlite3

import pytest

from app.infrastructure.database.connection import connect, migrate


def test_connect_enables_foreign_keys_and_busy_timeout(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        connection.close()


def test_participant_requires_existing_event(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        migrate(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO event_participants(id, event_id, display_name, added_at)
                VALUES (?, ?, ?, ?)
                """,
                ("participant-1", "missing-event", "Alice", "2026-10-12T00:00:00+00:00"),
            )
        connection.rollback()
        count = connection.execute(
            "SELECT COUNT(*) FROM event_participants"
        ).fetchone()[0]
        assert count == 0
    finally:
        connection.close()


def test_deleting_event_cascades_to_participants(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        migrate(connection)
        connection.execute(
            """
            INSERT INTO events(id, owner_id, title, starts_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("event-1", "owner-1", "Game night", None, "2026-10-12T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO event_participants(id, event_id, display_name, added_at)
            VALUES (?, ?, ?, ?)
            """,
            ("participant-1", "event-1", "Alice", "2026-10-12T00:00:00+00:00"),
        )
        connection.commit()

        connection.execute("DELETE FROM events WHERE id = ?", ("event-1",))
        connection.commit()

        count = connection.execute(
            "SELECT COUNT(*) FROM event_participants WHERE event_id = ?",
            ("event-1",),
        ).fetchone()[0]
        assert count == 0
    finally:
        connection.close()


def test_duplicate_participant_name_is_rejected_by_database(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        migrate(connection)
        connection.execute(
            """
            INSERT INTO events(id, owner_id, title, starts_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("event-1", "owner-1", "Game night", None, "2026-10-12T00:00:00+00:00"),
        )
        participant = (
            "participant-1",
            "event-1",
            "Alice",
            "2026-10-12T00:00:00+00:00",
        )
        connection.execute(
            """
            INSERT INTO event_participants(id, event_id, display_name, added_at)
            VALUES (?, ?, ?, ?)
            """,
            participant,
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO event_participants(id, event_id, display_name, added_at)
                VALUES (?, ?, ?, ?)
                """,
                ("participant-2", "event-1", "Alice", "2026-10-12T00:01:00+00:00"),
            )
    finally:
        connection.close()

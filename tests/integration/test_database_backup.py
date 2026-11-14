import sqlite3

import pytest

from app.infrastructure.database.backup import backup_database, restore_database
from app.infrastructure.database.connection import connect, migrate


def test_online_backup_and_restore_preserve_database_contents(tmp_path) -> None:
    source = tmp_path / "live.sqlite3"
    backup = tmp_path / "backups" / "gatherly.sqlite3"
    restored = tmp_path / "restore" / "gatherly.sqlite3"

    connection = connect(source)
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO events(id, owner_id, title, starts_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("event-1", "owner-1", "Backed up event", None, "2026-11-14T00:00:00Z"),
        )
        connection.commit()
    finally:
        connection.close()

    backup_database(source, backup)
    restore_database(backup, restored, confirm_application_stopped=True)

    connection = connect(restored)
    try:
        row = connection.execute(
            "SELECT title FROM events WHERE id = ?", ("event-1",)
        ).fetchone()
        assert row["title"] == "Backed up event"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_restore_requires_explicit_application_stopped_confirmation(tmp_path) -> None:
    with pytest.raises(ValueError, match="Stop the application"):
        restore_database(
            tmp_path / "missing.sqlite3",
            tmp_path / "restored.sqlite3",
            confirm_application_stopped=False,
        )


def test_backup_rejects_same_source_and_destination(tmp_path) -> None:
    source = tmp_path / "same.sqlite3"
    connection = connect(source)
    connection.close()

    with pytest.raises(ValueError, match="different files"):
        backup_database(source, source)

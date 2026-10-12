from __future__ import annotations

import sqlite3
from pathlib import Path


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL CHECK (length(trim(owner_id)) > 0),
            title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
            starts_at TEXT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_events_owner_created ON events(owner_id, created_at);
        """,
    ),
    (
        2,
        """
        CREATE TABLE event_participants (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
            added_at TEXT NOT NULL,
            UNIQUE(event_id, display_name)
        );
        CREATE INDEX idx_event_participants_event ON event_participants(event_id, added_at);
        """,
    ),
)


def connect(database_path: str | Path) -> sqlite3.Connection:
    path = str(database_path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    connection.commit()
    for version, sql in MIGRATIONS:
        applied = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone()
        if applied:
            continue
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + sql
                + f"\nINSERT INTO schema_migrations(version, applied_at) "
                f"VALUES ({version}, strftime('%Y-%m-%dT%H:%M:%fZ','now'));\nCOMMIT;"
            )
        except sqlite3.Error:
            connection.rollback()
            raise

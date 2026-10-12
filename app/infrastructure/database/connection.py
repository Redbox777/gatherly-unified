from contextlib import contextmanager
from pathlib import Path
import sqlite3
from collections.abc import Iterator


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
    (
        3,
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );

        CREATE TABLE auth_identities (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL CHECK (provider IN ('email', 'telegram')),
            provider_subject TEXT NOT NULL CHECK (length(trim(provider_subject)) > 0),
            password_hash TEXT NULL,
            verified_at TEXT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(provider, provider_subject),
            CHECK (
                (provider = 'email' AND password_hash IS NOT NULL)
                OR (provider = 'telegram' AND password_hash IS NULL)
            )
        );
        CREATE INDEX idx_auth_identities_user ON auth_identities(user_id);

        CREATE TABLE auth_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT NULL,
            CHECK (expires_at > created_at)
        );
        CREATE INDEX idx_auth_sessions_user_expiry
            ON auth_sessions(user_id, expires_at);

        CREATE TABLE auth_email_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            purpose TEXT NOT NULL CHECK (purpose IN ('verify_email', 'reset_password')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT NULL,
            CHECK (expires_at > created_at)
        );
        CREATE INDEX idx_auth_email_tokens_user_purpose
            ON auth_email_tokens(user_id, purpose);
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

@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a group of database operations atomically.

    The caller owns the transaction boundary. Repositories must not commit
    when this helper is used, because doing so would break atomicity.
    """
    if connection.in_transaction:
        # Nested use is deliberately rejected rather than silently committing
        # or accidentally taking ownership of a caller's transaction.
        raise RuntimeError("Cannot start a transaction while another is active")
    try:
        connection.execute("BEGIN")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


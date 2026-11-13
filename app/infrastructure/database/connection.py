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
    (
        4,
        """
        CREATE TABLE auth_telegram_login_payloads (
            payload_hash TEXT PRIMARY KEY,
            consumed_at TEXT NOT NULL
        );
        CREATE INDEX idx_auth_telegram_login_payloads_consumed
            ON auth_telegram_login_payloads(consumed_at);
        """,
    ),
    (
        5,
        """
        ALTER TABLE event_participants
        ADD COLUMN linked_user_id TEXT NULL REFERENCES users(id) ON DELETE SET NULL;
        CREATE INDEX idx_event_participants_linked_user
            ON event_participants(linked_user_id);
        """,
    ),
    (
        6,
        """
        CREATE TABLE participant_invitations (
            id TEXT PRIMARY KEY,
            participant_id TEXT NOT NULL
                REFERENCES event_participants(id) ON DELETE CASCADE,
            target_email TEXT NOT NULL
                CHECK (length(trim(target_email)) > 0),
            token_hash TEXT NOT NULL UNIQUE
                CHECK (length(trim(token_hash)) > 0),
            created_by_owner_id TEXT NOT NULL
                CHECK (length(trim(created_by_owner_id)) > 0),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            accepted_at TEXT NULL,
            revoked_at TEXT NULL,
            CHECK (expires_at > created_at),
            CHECK (accepted_at IS NULL OR revoked_at IS NULL)
        );
        CREATE INDEX idx_participant_invitations_participant
            ON participant_invitations(participant_id, created_at);
        CREATE INDEX idx_participant_invitations_target_email
            ON participant_invitations(target_email);
        """,
    ),
    (
        7,
        """
        CREATE TABLE smtp_rate_limit_events (
            id TEXT PRIMARY KEY,
            key_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_smtp_rate_limit_events_key_created
            ON smtp_rate_limit_events(key_hash, created_at);
        CREATE INDEX idx_smtp_rate_limit_events_created
            ON smtp_rate_limit_events(created_at);
        """,
    ),
    (
        8,
        """
        ALTER TABLE auth_email_tokens
        ADD COLUMN activated_at TEXT NULL;
        UPDATE auth_email_tokens
        SET activated_at = created_at
        WHERE activated_at IS NULL;
        CREATE INDEX idx_auth_email_tokens_active_user_purpose
            ON auth_email_tokens(user_id, purpose, activated_at, consumed_at);
        """,
    ),
    (
        9,
        """
        CREATE TABLE auth_email_tokens_new (
            id TEXT PRIMARY KEY,
            identity_id TEXT NOT NULL
                REFERENCES auth_identities(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            purpose TEXT NOT NULL CHECK (purpose IN ('verify_email', 'reset_password')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT NULL,
            activated_at TEXT NULL,
            CHECK (expires_at > created_at)
        );

        INSERT INTO auth_email_tokens_new(
            id, identity_id, token_hash, purpose, created_at, expires_at,
            consumed_at, activated_at
        )
        SELECT
            token.id,
            CASE
                WHEN (
                    SELECT COUNT(*) FROM auth_identities AS identity
                    WHERE identity.user_id = token.user_id
                      AND identity.provider = 'email'
                ) = 1
                THEN (
                    SELECT identity.id FROM auth_identities AS identity
                    WHERE identity.user_id = token.user_id
                      AND identity.provider = 'email'
                    LIMIT 1
                )
                ELSE NULL
            END,
            token.token_hash,
            token.purpose,
            token.created_at,
            token.expires_at,
            token.consumed_at,
            token.activated_at
        FROM auth_email_tokens AS token;

        DROP TABLE auth_email_tokens;
        ALTER TABLE auth_email_tokens_new RENAME TO auth_email_tokens;

        CREATE INDEX idx_auth_email_tokens_identity_purpose
            ON auth_email_tokens(identity_id, purpose);
        CREATE INDEX idx_auth_email_tokens_active_identity_purpose
            ON auth_email_tokens(identity_id, purpose, activated_at, consumed_at);
        """,
    ),
    (
        10,
        """
        CREATE TABLE http_rate_limit_events (
            id TEXT PRIMARY KEY,
            key_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_http_rate_limit_events_key_created
            ON http_rate_limit_events(key_hash, created_at);
        CREATE INDEX idx_http_rate_limit_events_created
            ON http_rate_limit_events(created_at);
        """,
    ),
    (
        11,
        """
        CREATE TABLE notifications (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event_id TEXT NULL REFERENCES events(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (
                kind IN ('event_updated', 'participant_added', 'event_reminder')
            ),
            title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
            body TEXT NOT NULL CHECK (length(trim(body)) BETWEEN 1 AND 1000),
            dedupe_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            read_at TEXT NULL,
            email_sent_at TEXT NULL,
            email_attempts INTEGER NOT NULL DEFAULT 0 CHECK (email_attempts >= 0),
            email_last_error TEXT NULL
        );
        CREATE INDEX idx_notifications_user_created
            ON notifications(user_id, created_at DESC);
        CREATE INDEX idx_notifications_email_pending
            ON notifications(email_sent_at, email_attempts, created_at);

        CREATE TABLE notification_preferences (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            email_enabled INTEGER NOT NULL DEFAULT 1 CHECK (email_enabled IN (0, 1)),
            event_updates_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (event_updates_enabled IN (0, 1)),
            participant_added_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (participant_added_enabled IN (0, 1)),
            reminders_enabled INTEGER NOT NULL DEFAULT 1 CHECK (reminders_enabled IN (0, 1)),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE event_reminder_preferences (
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            owner_id TEXT NOT NULL CHECK (length(trim(owner_id)) > 0),
            offset_minutes INTEGER NOT NULL CHECK (offset_minutes IN (1440, 60, 15)),
            created_at TEXT NOT NULL,
            PRIMARY KEY(event_id, offset_minutes)
        );
        CREATE INDEX idx_event_reminders_owner
            ON event_reminder_preferences(owner_id, event_id);
        """,
    ),
)

 
def connect(database_path: str | Path) -> sqlite3.Connection:
    """Open an independent SQLite connection for one unit of application work.

    HTTP requests each receive their own connection. check_same_thread=False
    is retained because FastAPI may create a synchronous dependency in one
    worker thread and execute its endpoint in another; the connection is never
    shared between different requests.
    """
    path = str(database_path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _execute_migration(connection: sqlite3.Connection, script: str) -> None:
    """Execute a migration script without implicitly committing its transaction."""
    pending: list[str] = []
    for line in script.splitlines():
        pending.append(line)
        statement = "\n".join(pending)
        if sqlite3.complete_statement(statement):
            if statement.strip():
                connection.execute(statement)
            pending.clear()
    if any(line.strip() for line in pending):
        raise sqlite3.OperationalError("Incomplete SQL migration statement")


def migrate(connection: sqlite3.Connection) -> None:
    """Apply each migration while holding SQLite's cross-connection write lock."""
    with transaction(connection):
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )

    for version, sql in MIGRATIONS:
        # BEGIN IMMEDIATE serializes migration runners. Re-check the version
        # only after obtaining the lock so a second process sees the first
        # process's committed migration instead of applying it twice.
        with transaction(connection):
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if applied:
                continue
            _execute_migration(connection, sql)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (version,),
            )


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a group of database operations atomically.

    Each request owns its connection, so transaction state cannot be changed
    by another request. Nested transactions remain deliberately unsupported.
    """
    if connection.in_transaction:
        raise RuntimeError("Cannot start a transaction while another is active")
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

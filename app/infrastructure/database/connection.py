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
    (
        12,
        """
        CREATE TABLE event_notes (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            author_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content TEXT NOT NULL CHECK (length(trim(content)) BETWEEN 1 AND 5000),
            visibility TEXT NOT NULL CHECK (visibility IN ('private', 'shared')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_event_notes_event_created
            ON event_notes(event_id, created_at, id);
        CREATE INDEX idx_event_notes_author
            ON event_notes(author_id, event_id);
        """,
    ),
    (
        13,
        """
        CREATE TABLE event_tasks (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 160),
            description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 2000),
            due_at TEXT NULL,
            assignee_participant_id TEXT NULL
                REFERENCES event_participants(id) ON DELETE SET NULL,
            completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_event_tasks_event_status_due
            ON event_tasks(event_id, completed, due_at, created_at);
        CREATE INDEX idx_event_tasks_assignee
            ON event_tasks(assignee_participant_id);
        """,
    ),
    (
        14,
        """
        CREATE TABLE event_venues (
            event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
            venue_name TEXT NOT NULL CHECK (length(trim(venue_name)) BETWEEN 1 AND 160),
            address TEXT NOT NULL CHECK (length(trim(address)) BETWEEN 1 AND 500),
            latitude REAL NULL CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
            longitude REAL NULL CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
            CHECK ((latitude IS NULL) = (longitude IS NULL))
        );
        CREATE INDEX idx_event_venues_coordinates ON event_venues(latitude, longitude);
        """,
    ),
    (
        15,
        """
        CREATE TABLE event_expenses (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 160),
            description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
            amount_minor_units INTEGER NOT NULL CHECK (amount_minor_units > 0),
            currency TEXT NOT NULL CHECK (length(currency) = 3),
            paid_by_participant_id TEXT NULL
                REFERENCES event_participants(id) ON DELETE SET NULL,
            incurred_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_event_expenses_event_incurred
            ON event_expenses(event_id, incurred_at DESC, created_at DESC);
        CREATE INDEX idx_event_expenses_payer
            ON event_expenses(paid_by_participant_id);
        """,
    ),
    (
        16,
        """
        CREATE TABLE event_expense_splits (
            expense_id TEXT PRIMARY KEY
                REFERENCES event_expenses(id) ON DELETE CASCADE,
            mode TEXT NOT NULL CHECK (mode IN ('equal', 'fixed', 'weighted'))
        );
        CREATE TABLE event_expense_allocations (
            id TEXT PRIMARY KEY,
            expense_id TEXT NOT NULL
                REFERENCES event_expenses(id) ON DELETE CASCADE,
            participant_id TEXT NULL
                REFERENCES event_participants(id) ON DELETE SET NULL,
            participant_name TEXT NOT NULL
                CHECK (length(trim(participant_name)) BETWEEN 1 AND 120),
            amount_minor_units INTEGER NOT NULL CHECK (amount_minor_units >= 0),
            weight INTEGER NULL CHECK (weight IS NULL OR weight > 0)
        );
        CREATE INDEX idx_event_expense_allocations_expense
            ON event_expense_allocations(expense_id, participant_name);
        CREATE INDEX idx_event_expense_allocations_participant
            ON event_expense_allocations(participant_id);
        """,
    ),
    (
        17,
        """
        ALTER TABLE event_expenses
        ADD COLUMN paid_by_participant_name TEXT NULL;
        UPDATE event_expenses
        SET paid_by_participant_name = (
            SELECT display_name FROM event_participants
            WHERE event_participants.id = event_expenses.paid_by_participant_id
        )
        WHERE paid_by_participant_id IS NOT NULL;

        CREATE TABLE event_settlements (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            from_participant_id TEXT NULL
                REFERENCES event_participants(id) ON DELETE SET NULL,
            from_participant_name TEXT NOT NULL
                CHECK (length(trim(from_participant_name)) BETWEEN 1 AND 120),
            to_participant_id TEXT NULL
                REFERENCES event_participants(id) ON DELETE SET NULL,
            to_participant_name TEXT NOT NULL
                CHECK (length(trim(to_participant_name)) BETWEEN 1 AND 120),
            amount_minor_units INTEGER NOT NULL CHECK (amount_minor_units > 0),
            currency TEXT NOT NULL CHECK (length(currency) = 3),
            settled_at TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 1000),
            created_at TEXT NOT NULL,
            CHECK (
                from_participant_id IS NULL OR to_participant_id IS NULL
                OR from_participant_id != to_participant_id
            )
        );
        CREATE INDEX idx_event_settlements_event_currency_date
            ON event_settlements(event_id, currency, settled_at, id);
        CREATE INDEX idx_event_settlements_from_participant
            ON event_settlements(from_participant_id);
        CREATE INDEX idx_event_settlements_to_participant
            ON event_settlements(to_participant_id);

        CREATE TRIGGER snapshot_event_expense_payer_after_insert
        AFTER INSERT ON event_expenses
        WHEN NEW.paid_by_participant_id IS NOT NULL
        BEGIN
            UPDATE event_expenses
            SET paid_by_participant_name = (
                SELECT display_name FROM event_participants
                WHERE id = NEW.paid_by_participant_id
            )
            WHERE id = NEW.id;
        END;

        CREATE TRIGGER snapshot_event_expense_payer_after_update
        AFTER UPDATE OF paid_by_participant_id ON event_expenses
        WHEN NEW.paid_by_participant_id IS NOT NULL
        BEGIN
            UPDATE event_expenses
            SET paid_by_participant_name = (
                SELECT display_name FROM event_participants
                WHERE id = NEW.paid_by_participant_id
            )
            WHERE id = NEW.id;
        END;

        CREATE TRIGGER preserve_participant_finance_names_before_delete
        BEFORE DELETE ON event_participants
        BEGIN
            UPDATE event_expenses
            SET paid_by_participant_name = OLD.display_name
            WHERE paid_by_participant_id = OLD.id;
            UPDATE event_settlements
            SET from_participant_name = OLD.display_name
            WHERE from_participant_id = OLD.id;
            UPDATE event_settlements
            SET to_participant_name = OLD.display_name
            WHERE to_participant_id = OLD.id;
        END;
        """,
    ),
    (
        18,
        """
        CREATE TABLE bug_reports (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            app_version TEXT NOT NULL CHECK (length(app_version) BETWEEN 1 AND 32),
            browser TEXT NOT NULL CHECK (browser IN ('Chrome', 'Firefox', 'Safari', 'Edge', 'Other')),
            operating_system TEXT NOT NULL CHECK (operating_system IN ('Windows', 'macOS', 'Linux', 'Android', 'iOS', 'Other')),
            module TEXT NOT NULL CHECK (module IN ('frontend', 'manual')),
            error_code TEXT NOT NULL CHECK (error_code IN ('window_error', 'unhandled_rejection', 'user_reported_issue')),
            stack TEXT NOT NULL DEFAULT '' CHECK (length(stack) <= 1000)
        );
        CREATE INDEX idx_bug_reports_created_at ON bug_reports(created_at DESC);
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

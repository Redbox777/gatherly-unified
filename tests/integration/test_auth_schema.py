import sqlite3
from datetime import datetime

import pytest

from app.domain.auth_tokens import hash_token
from app.infrastructure.database.auth_email_tokens_repository import AuthEmailTokensRepository
from app.infrastructure.database.connection import (
    MIGRATIONS,
    _execute_migration,
    connect,
    migrate,
    transaction,
)


def test_auth_migration_creates_account_identity_and_session_tables(tmp_path) -> None:
    connection = connect(tmp_path / "auth.sqlite3")
    try:
        migrate(connection)
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "users",
            "auth_identities",
            "auth_sessions",
            "auth_email_tokens",
            "auth_telegram_login_payloads",
            "participant_invitations",
            "smtp_rate_limit_events",
            "http_rate_limit_events",
            "event_tasks",
        } <= tables

        token_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(auth_email_tokens)").fetchall()
        }
        assert "activated_at" in token_columns
        assert "identity_id" in token_columns
        assert "user_id" not in token_columns
        token_column_details = {
            row["name"]: row for row in connection.execute(
                "PRAGMA table_info(auth_email_tokens)"
            )
        }
        assert token_column_details["identity_id"]["notnull"] == 1
        token_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(auth_email_tokens)"
        ).fetchall()
        assert any(
            row["table"] == "auth_identities"
            and row["from"] == "identity_id"
            and row["to"] == "id"
            for row in token_foreign_keys
        )

        versions = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert versions == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
    finally:
        connection.close()




def test_migration_preserves_existing_active_email_tokens(tmp_path) -> None:
    connection = connect(tmp_path / "legacy-auth.sqlite3")
    try:
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.commit()
        for version, script in MIGRATIONS[:8]:
            with transaction(connection):
                _execute_migration(connection, script)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, "2026-10-12T00:00:00Z"),
                )

        created_at = "2026-10-12T12:00:00.000000Z"
        expires_at = "2026-10-13T12:00:00.000000Z"
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("legacy-user", created_at),
        )
        connection.execute(
            """INSERT INTO auth_identities(
                id, user_id, provider, provider_subject, password_hash,
                verified_at, created_at
            ) VALUES (?, ?, 'email', ?, ?, NULL, ?)""",
            (
                "legacy-identity",
                "legacy-user",
                "legacy@example.test",
                "password-hash",
                created_at,
            ),
        )
        raw_token = "legacy-verification-token"
        connection.execute(
            """INSERT INTO auth_email_tokens(
                id, user_id, token_hash, purpose, created_at, expires_at,
                consumed_at, activated_at
            ) VALUES (?, ?, ?, 'verify_email', ?, ?, NULL, ?)""",
            (
                "legacy-token",
                "legacy-user",
                hash_token(raw_token),
                created_at,
                expires_at,
                created_at,
            ),
        )
        connection.commit()

        migrate(connection)

        stored = connection.execute(
            "SELECT activated_at, consumed_at FROM auth_email_tokens WHERE id = ?",
            ("legacy-token",),
        ).fetchone()
        assert stored["activated_at"] == created_at
        assert stored["consumed_at"] is None
        assert AuthEmailTokensRepository(connection).consume_verification_token(
            raw_token=raw_token,
            now=datetime.fromisoformat("2026-10-12T12:01:00+00:00"),
        )
    finally:
        connection.close()


def test_auth_identity_provider_subject_must_be_unique(tmp_path) -> None:
    connection = connect(tmp_path / "auth.sqlite3")
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("user-1", "2026-10-12T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("user-2", "2026-10-12T00:00:00Z"),
        )
        identity = (
            "identity-1",
            "user-1",
            "email",
            "user@example.test",
            "argon2id-placeholder-hash",
            "2026-10-12T00:00:00Z",
            "2026-10-12T00:00:00Z",
        )
        connection.execute(
            """
            INSERT INTO auth_identities(
                id, user_id, provider, provider_subject, password_hash, verified_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            identity,
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO auth_identities(
                    id, user_id, provider, provider_subject, password_hash, verified_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "identity-2",
                    "user-2",
                    "email",
                    "user@example.test",
                    "another-hash",
                    None,
                    "2026-10-12T00:00:00Z",
                ),
            )
    finally:
        connection.close()


def test_telegram_identity_cannot_store_password_hash(tmp_path) -> None:
    connection = connect(tmp_path / "auth.sqlite3")
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("user-1", "2026-10-12T00:00:00Z"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO auth_identities(
                    id, user_id, provider, provider_subject, password_hash, verified_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "identity-1",
                    "user-1",
                    "telegram",
                    "telegram-123",
                    "not-allowed",
                    "2026-10-12T00:00:00Z",
                    "2026-10-12T00:00:00Z",
                ),
            )
    finally:
        connection.close()


def test_auth_session_stores_unique_token_hash_and_expiry(tmp_path) -> None:
    connection = connect(tmp_path / "auth.sqlite3")
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("user-1", "2026-10-12T00:00:00Z"),
        )
        session = (
            "session-1",
            "user-1",
            "sha256:token-digest",
            "2026-10-12T00:00:00Z",
            "2026-10-13T00:00:00Z",
            None,
        )
        connection.execute(
            """
            INSERT INTO auth_sessions(
                id, user_id, token_hash, created_at, expires_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            session,
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO auth_sessions(
                    id, user_id, token_hash, created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "session-2",
                    "user-1",
                    "sha256:token-digest",
                    "2026-10-12T01:00:00Z",
                    "2026-10-13T01:00:00Z",
                    None,
                ),
            )
    finally:
        connection.close()



def test_identity_token_migration_refuses_ambiguous_legacy_ownership(tmp_path) -> None:
    connection = connect(tmp_path / "ambiguous-legacy-auth.sqlite3")
    try:
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.commit()
        for version, script in MIGRATIONS[:8]:
            with transaction(connection):
                _execute_migration(connection, script)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, "2026-10-12T00:00:00Z"),
                )

        created_at = "2026-10-12T12:00:00.000000Z"
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("ambiguous-user", created_at),
        )
        for identity_id, email in (
            ("email-identity-a", "a@example.test"),
            ("email-identity-b", "b@example.test"),
        ):
            connection.execute(
                """INSERT INTO auth_identities(
                    id, user_id, provider, provider_subject, password_hash,
                    verified_at, created_at
                ) VALUES (?, ?, 'email', ?, ?, NULL, ?)""",
                (identity_id, "ambiguous-user", email, "password-hash", created_at),
            )
        connection.execute(
            """INSERT INTO auth_email_tokens(
                id, user_id, token_hash, purpose, created_at, expires_at,
                consumed_at, activated_at
            ) VALUES (?, ?, ?, 'verify_email', ?, ?, NULL, ?)""",
            (
                "ambiguous-token",
                "ambiguous-user",
                hash_token("legacy-ambiguous-token"),
                created_at,
                "2026-10-13T12:00:00.000000Z",
                created_at,
            ),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            migrate(connection)

        versions = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        assert versions == {1, 2, 3, 4, 5, 6, 7, 8}
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(auth_email_tokens)")
        }
        assert "user_id" in columns
        assert "identity_id" not in columns
        assert connection.execute(
            "SELECT 1 FROM auth_email_tokens WHERE id = 'ambiguous-token'"
        ).fetchone() is not None
    finally:
        connection.close()

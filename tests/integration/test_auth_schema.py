import sqlite3

import pytest

from app.infrastructure.database.connection import connect, migrate


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
        } <= tables

        versions = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert versions == {1, 2, 3, 4, 5, 6}
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

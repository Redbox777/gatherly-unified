import sqlite3

import pytest

from app.infrastructure.database import connection as database_connection
from app.infrastructure.database.connection import MIGRATIONS, connect, migrate, transaction


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


def test_transaction_commits_successful_work(tmp_path) -> None:
    connection = connect(tmp_path / "transaction.sqlite3")
    try:
        migrate(connection)
        with transaction(connection):
            connection.execute(
                """
                INSERT INTO events(id, owner_id, title, starts_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("event-1", "owner-1", "Game night", None, "2026-10-12T00:00:00+00:00"),
            )
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    finally:
        connection.close()


def test_transaction_rolls_back_all_work_on_error(tmp_path) -> None:
    connection = connect(tmp_path / "transaction.sqlite3")
    try:
        migrate(connection)
        with pytest.raises(RuntimeError, match="abort transaction"):
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO events(id, owner_id, title, starts_at, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("event-1", "owner-1", "Game night", None, "2026-10-12T00:00:00+00:00"),
                )
                raise RuntimeError("abort transaction")
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    finally:
        connection.close()


def test_transaction_rejects_nested_transaction(tmp_path) -> None:
    connection = connect(tmp_path / "transaction.sqlite3")
    try:
        migrate(connection)
        with transaction(connection):
            with pytest.raises(RuntimeError, match="another is active"):
                with transaction(connection):
                    pass
    finally:
        connection.close()


def test_transaction_rolls_back_related_event_and_participants(tmp_path) -> None:
    connection = connect(tmp_path / "transaction.sqlite3")
    try:
        migrate(connection)
        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
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
                connection.execute(
                    """
                    INSERT INTO event_participants(id, event_id, display_name, added_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("participant-2", "event-1", "Alice", "2026-10-12T00:01:00+00:00"),
                )

        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM event_participants").fetchone()[0] == 0
    finally:
        connection.close()


def _insert_event_and_participant(connection: sqlite3.Connection) -> None:
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


def _insert_invitation(
    connection: sqlite3.Connection,
    *,
    invitation_id: str = "invitation-1",
    token_hash: str = "sha256:token-digest",
    expires_at: str = "2026-10-19T00:00:00+00:00",
    accepted_at: str | None = None,
    revoked_at: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO participant_invitations(
            id, participant_id, target_email, token_hash, created_by_owner_id,
            created_at, expires_at, accepted_at, revoked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invitation_id,
            "participant-1",
            "alice@example.test",
            token_hash,
            "owner-1",
            "2026-10-12T00:00:00+00:00",
            expires_at,
            accepted_at,
            revoked_at,
        ),
    )


def test_invitation_requires_existing_participant(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        migrate(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_invitation(connection)
    finally:
        connection.close()


def test_invitation_token_hash_must_be_unique(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        migrate(connection)
        _insert_event_and_participant(connection)
        _insert_invitation(connection)
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            _insert_invitation(
                connection,
                invitation_id="invitation-2",
            )
    finally:
        connection.close()


def test_invitation_expiry_must_be_after_creation(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        migrate(connection)
        _insert_event_and_participant(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_invitation(
                connection,
                expires_at="2026-10-12T00:00:00+00:00",
            )
    finally:
        connection.close()


def test_invitation_cannot_be_accepted_and_revoked(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        migrate(connection)
        _insert_event_and_participant(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_invitation(
                connection,
                accepted_at="2026-10-13T00:00:00+00:00",
                revoked_at="2026-10-14T00:00:00+00:00",
            )
    finally:
        connection.close()


def test_deleting_participant_cascades_to_invitations(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        migrate(connection)
        _insert_event_and_participant(connection)
        _insert_invitation(connection)
        connection.commit()

        connection.execute(
            "DELETE FROM event_participants WHERE id = ?",
            ("participant-1",),
        )
        connection.commit()

        count = connection.execute(
            "SELECT COUNT(*) FROM participant_invitations"
        ).fetchone()[0]
        assert count == 0
    finally:
        connection.close()



def test_concurrent_migration_runners_apply_each_version_once(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database_path = tmp_path / "concurrent-migrations.sqlite3"
    start = Barrier(2)

    def run_migrations() -> list[int]:
        connection = connect(database_path)
        try:
            start.wait(timeout=5)
            migrate(connection)
            return [
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_migrations(), range(2)))

    expected = [version for version, _ in MIGRATIONS]
    assert results == [expected, expected]


def test_finance_migration_17_backfills_and_preserves_legacy_payer_name(
    tmp_path, monkeypatch
) -> None:
    connection = connect(tmp_path / "finance-migration-upgrade.sqlite3")
    all_migrations = database_connection.MIGRATIONS
    try:
        monkeypatch.setattr(database_connection, "MIGRATIONS", all_migrations[:16])
        migrate(connection)
        connection.execute(
            """
            INSERT INTO events(id, owner_id, title, starts_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("legacy-event", "owner-1", "Legacy event", None, "2026-11-01T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO event_participants(id, event_id, display_name, added_at)
            VALUES (?, ?, ?, ?)
            """,
            ("legacy-payer", "legacy-event", "Original payer", "2026-11-01T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO event_expenses(
                id, event_id, title, description, amount_minor_units, currency,
                paid_by_participant_id, incurred_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-expense", "legacy-event", "Legacy purchase", "",
                2500, "EUR", "legacy-payer",
                "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z",
                "2026-11-01T00:00:00Z",
            ),
        )
        connection.commit()

        monkeypatch.setattr(database_connection, "MIGRATIONS", all_migrations)
        migrate(connection)

        migrated = connection.execute(
            """
            SELECT paid_by_participant_id, paid_by_participant_name
            FROM event_expenses WHERE id = ?
            """,
            ("legacy-expense",),
        ).fetchone()
        assert migrated["paid_by_participant_id"] == "legacy-payer"
        assert migrated["paid_by_participant_name"] == "Original payer"

        connection.execute(
            "DELETE FROM event_participants WHERE id = ?", ("legacy-payer",)
        )
        preserved = connection.execute(
            """
            SELECT paid_by_participant_id, paid_by_participant_name
            FROM event_expenses WHERE id = ?
            """,
            ("legacy-expense",),
        ).fetchone()
        assert preserved["paid_by_participant_id"] is None
        assert preserved["paid_by_participant_name"] == "Original payer"

        applied_versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert applied_versions == list(range(1, 18))
    finally:
        connection.close()

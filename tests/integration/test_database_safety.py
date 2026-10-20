import sqlite3

import pytest

from app.infrastructure.database.connection import connect, migrate, transaction


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

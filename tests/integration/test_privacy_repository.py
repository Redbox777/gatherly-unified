from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.privacy_repository import PrivacyRepository
from app.infrastructure.database.erasure_ledger import AccountErasureLedger


NOW = "2026-11-15T00:00:00Z"


def test_account_erasure_anonymizes_shared_participant_finance_records(tmp_path) -> None:
    connection = connect(tmp_path / "privacy-erasure.sqlite3")
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("deleting-user", NOW),
        )
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("event-owner", NOW),
        )
        connection.execute(
            """INSERT INTO auth_identities(
                id, user_id, provider, provider_subject, password_hash,
                verified_at, created_at
            ) VALUES (?, ?, 'email', ?, ?, ?, ?)""",
            (
                "identity-1",
                "deleting-user",
                "delete-me@example.test",
                "not-exported-hash",
                NOW,
                NOW,
            ),
        )
        connection.execute(
            """INSERT INTO events(id, owner_id, title, starts_at, created_at)
               VALUES (?, ?, ?, NULL, ?)""",
            ("shared-event", "event-owner", "Shared event", NOW),
        )
        connection.execute(
            """INSERT INTO event_participants(
                id, event_id, display_name, added_at, linked_user_id
            ) VALUES (?, ?, ?, ?, ?)""",
            ("participant-12345678", "shared-event", "Private name", NOW, "deleting-user"),
        )
        connection.execute(
            """INSERT INTO event_expenses(
                id, event_id, title, description, amount_minor_units, currency,
                paid_by_participant_id, incurred_at, created_at, updated_at
            ) VALUES (?, ?, ?, '', 1000, 'EUR', ?, ?, ?, ?)""",
            (
                "expense-1",
                "shared-event",
                "Dinner",
                "participant-12345678",
                NOW,
                NOW,
                NOW,
            ),
        )
        connection.execute(
            """INSERT INTO event_expense_allocations(
                id, expense_id, participant_id, participant_name,
                amount_minor_units, weight
            ) VALUES (?, ?, ?, ?, 1000, NULL)""",
            (
                "allocation-1",
                "expense-1",
                "participant-12345678",
                "Private name",
            ),
        )
        connection.execute(
            """INSERT INTO event_settlements(
                id, event_id, from_participant_id, from_participant_name,
                to_participant_id, to_participant_name, amount_minor_units,
                currency, settled_at, note, created_at
            ) VALUES (?, ?, ?, ?, NULL, 'Event owner', 1000, 'EUR', ?, '', ?)""",
            (
                "settlement-1",
                "shared-event",
                "participant-12345678",
                "Private name",
                NOW,
                NOW,
            ),
        )
        connection.execute(
            """INSERT INTO participant_invitations(
                id, participant_id, target_email, token_hash, created_by_owner_id,
                created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "invitation-1",
                "participant-12345678",
                "delete-me@example.test",
                "secret-token-hash",
                "event-owner",
                NOW,
                "2026-11-22T00:00:00Z",
            ),
        )
        connection.commit()

        deleted = PrivacyRepository(connection).delete_user_account(
            user_id="deleting-user",
            erasure_ledger=AccountErasureLedger(tmp_path / "erasure-ledger", b"e" * 32),
        )

        assert deleted is True
        participant = connection.execute(
            "SELECT display_name, linked_user_id FROM event_participants WHERE id = ?",
            ("participant-12345678",),
        ).fetchone()
        assert participant["display_name"] == "Former member particip"
        assert participant["linked_user_id"] is None
        assert connection.execute(
            "SELECT paid_by_participant_name FROM event_expenses WHERE id = ?",
            ("expense-1",),
        ).fetchone()[0] == "Former member particip"
        assert connection.execute(
            "SELECT participant_name FROM event_expense_allocations WHERE id = ?",
            ("allocation-1",),
        ).fetchone()[0] == "Former member particip"
        assert connection.execute(
            "SELECT from_participant_name FROM event_settlements WHERE id = ?",
            ("settlement-1",),
        ).fetchone()[0] == "Former member particip"
        assert connection.execute(
            "SELECT COUNT(*) FROM participant_invitations WHERE id = ?",
            ("invitation-1",),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM users WHERE id = ?", ("deleting-user",)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE id = ?", ("shared-event",)
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_account_export_excludes_password_hashes_and_invitation_token_hashes(
    tmp_path,
) -> None:
    connection = connect(tmp_path / "privacy-export.sqlite3")
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("export-user", NOW),
        )
        connection.execute(
            """INSERT INTO auth_identities(
                id, user_id, provider, provider_subject, password_hash,
                verified_at, created_at
            ) VALUES (?, ?, 'email', ?, ?, ?, ?)""",
            (
                "identity-1",
                "export-user",
                "export@example.test",
                "secret-password-hash",
                NOW,
                NOW,
            ),
        )
        connection.commit()

        exported = PrivacyRepository(connection).export_user_data(
            user_id="export-user"
        )

        assert exported is not None
        assert exported["identities"] == [
            {
                "provider": "email",
                "provider_subject": "export@example.test",
                "verified_at": NOW,
                "created_at": NOW,
            }
        ]
        assert "password_hash" not in str(exported)
        assert "secret-password-hash" not in str(exported)
        assert "token_hash" not in str(exported)
    finally:
        connection.close()


def test_account_export_returns_none_for_missing_user(tmp_path) -> None:
    connection = connect(tmp_path / "privacy-missing.sqlite3")
    try:
        migrate(connection)
        assert PrivacyRepository(connection).export_user_data(
            user_id="missing-user"
        ) is None
    finally:
        connection.close()

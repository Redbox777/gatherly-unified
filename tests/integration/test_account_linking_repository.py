from datetime import UTC, datetime
import pytest

from app.infrastructure.database.account_linking_repository import (
    AccountLinkConflict,
    AccountLinkingRepository,
)
from app.infrastructure.database.auth_identities_repository import AuthIdentitiesRepository
from app.infrastructure.database.connection import connect, migrate


NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path):
    connection = connect(tmp_path / "account-linking.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


def create_identity(connection, *, provider, subject, user_email_hash=None):
    return AuthIdentitiesRepository(connection).create_user_with_identity(
        provider=provider,
        provider_subject=subject,
        password_hash=user_email_hash if provider == "email" else None,
        now=NOW,
        verified_at=NOW,
    )


def test_merge_moves_identities_and_user_owned_data_atomically(database) -> None:
    target_telegram = create_identity(
        database, provider="telegram", subject="telegram-target"
    )
    source_email = create_identity(
        database,
        provider="email",
        subject="source@example.test",
        user_email_hash="argon2id$source",
    )
    event_id = "event-source"
    participant_id = "participant-source"
    database.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (event_id, source_email.user_id, "Source event", None, NOW.isoformat()),
    )
    database.execute(
        """INSERT INTO event_participants(
               id, event_id, display_name, added_at, linked_user_id
           ) VALUES (?, ?, ?, ?, ?)""",
        (participant_id, event_id, "Source participant", NOW.isoformat(), source_email.user_id),
    )
    database.execute(
        """INSERT INTO event_notes(
               id, event_id, author_id, content, visibility, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("note-source", event_id, source_email.user_id, "Keep this note", "private",
         NOW.isoformat(), NOW.isoformat()),
    )
    database.execute(
        """INSERT INTO notifications(
               id, user_id, event_id, kind, title, body, dedupe_key, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("notification-source", source_email.user_id, event_id, "event_updated",
         "Updated", "Event changed", "source-notification", NOW.isoformat()),
    )
    database.execute(
        """INSERT INTO notification_preferences(user_id, updated_at)
           VALUES (?, ?)""",
        (source_email.user_id, NOW.isoformat()),
    )
    database.execute(
        """INSERT INTO event_reminder_preferences(event_id, owner_id, offset_minutes, created_at)
           VALUES (?, ?, ?, ?)""",
        (event_id, source_email.user_id, 60, NOW.isoformat()),
    )
    database.commit()

    AccountLinkingRepository(database).merge_accounts(
        target_user_id=target_telegram.user_id,
        source_user_id=source_email.user_id,
        proof_identity_id=source_email.identity_id,
        proof_provider="email",
        expected_password_hash="argon2id$source",
        now=NOW,
    )

    assert database.execute(
        "SELECT user_id FROM auth_identities WHERE id = ?", (source_email.identity_id,)
    ).fetchone()["user_id"] == target_telegram.user_id
    assert database.execute(
        "SELECT owner_id FROM events WHERE id = ?", (event_id,)
    ).fetchone()["owner_id"] == target_telegram.user_id
    assert database.execute(
        "SELECT author_id FROM event_notes WHERE id = ?", ("note-source",)
    ).fetchone()["author_id"] == target_telegram.user_id
    assert database.execute(
        "SELECT user_id FROM notifications WHERE id = ?", ("notification-source",)
    ).fetchone()["user_id"] == target_telegram.user_id
    assert database.execute(
        "SELECT linked_user_id FROM event_participants WHERE id = ?", (participant_id,)
    ).fetchone()["linked_user_id"] == target_telegram.user_id
    assert database.execute(
        "SELECT owner_id FROM event_reminder_preferences WHERE event_id = ?", (event_id,)
    ).fetchone()["owner_id"] == target_telegram.user_id
    assert database.execute(
        "SELECT 1 FROM users WHERE id = ?", (source_email.user_id,)
    ).fetchone() is None
    assert database.execute(
        "SELECT user_id FROM notification_preferences"
    ).fetchone()["user_id"] == target_telegram.user_id


def test_merge_rejects_provider_collision_without_partial_changes(database) -> None:
    target = create_identity(
        database, provider="email", subject="target@example.test",
        user_email_hash="argon2id$target",
    )
    source = create_identity(
        database, provider="email", subject="source@example.test",
        user_email_hash="argon2id$source",
    )

    with pytest.raises(AccountLinkConflict, match="same provider"):
        AccountLinkingRepository(database).merge_accounts(
            target_user_id=target.user_id,
            source_user_id=source.user_id,
            proof_identity_id=source.identity_id,
            proof_provider="email",
            expected_password_hash="argon2id$source",
            now=NOW,
        )

    assert database.execute(
        "SELECT user_id FROM auth_identities WHERE id = ?", (source.identity_id,)
    ).fetchone()["user_id"] == source.user_id
    assert database.execute(
        "SELECT 1 FROM users WHERE id = ?", (source.user_id,)
    ).fetchone() is not None


def test_merge_rejects_stale_password_proof_and_rolls_back(database) -> None:
    target = create_identity(database, provider="telegram", subject="telegram-target")
    source = create_identity(
        database, provider="email", subject="source@example.test",
        user_email_hash="argon2id$current",
    )

    with pytest.raises(AccountLinkConflict, match="re-verified"):
        AccountLinkingRepository(database).merge_accounts(
            target_user_id=target.user_id,
            source_user_id=source.user_id,
            proof_identity_id=source.identity_id,
            proof_provider="email",
            expected_password_hash="argon2id$stale",
            now=NOW,
        )

    assert database.execute(
        "SELECT user_id FROM auth_identities WHERE id = ?", (source.identity_id,)
    ).fetchone()["user_id"] == source.user_id


def test_merge_rejects_unknown_or_same_user(database) -> None:
    target = create_identity(database, provider="telegram", subject="telegram-target")

    with pytest.raises(AccountLinkConflict, match="already linked"):
        AccountLinkingRepository(database).merge_accounts(
            target_user_id=target.user_id,
            source_user_id=target.user_id,
            proof_identity_id=target.identity_id,
            proof_provider="telegram",
            expected_password_hash=None,
            now=NOW,
        )

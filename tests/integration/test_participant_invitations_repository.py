from datetime import UTC, datetime, timedelta
import sqlite3
from uuid import UUID

import pytest

from app.domain.participant_invitations import ParticipantInvitation
from app.infrastructure.database.connection import migrate
from app.infrastructure.database.participant_invitations_repository import (
    ParticipantInvitationsRepository,
)


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
EVENT_ID = UUID("00000000-0000-0000-0000-000000000010")
PARTICIPANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def connection():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    migrate(db)
    db.execute(
        """INSERT INTO events(id, owner_id, title, starts_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (str(EVENT_ID), "owner-1", "Game night", None, NOW.isoformat()),
    )
    db.execute(
        """INSERT INTO event_participants(
               id, event_id, display_name, added_at, linked_user_id
           ) VALUES (?, ?, ?, ?, NULL)""",
        (str(PARTICIPANT_ID), str(EVENT_ID), "Alice", NOW.isoformat()),
    )
    db.commit()
    yield db
    db.close()


def _invitation(
    *,
    token_hash: str = "digest-1",
    target_email: str = "alice@example.test",
    now: datetime = NOW,
) -> ParticipantInvitation:
    return ParticipantInvitation.create(
        participant_id=PARTICIPANT_ID,
        target_email=target_email,
        token_hash=token_hash,
        created_by_owner_id="owner-1",
        now=now,
    )


def _verified_user(connection: sqlite3.Connection, *, email: str) -> str:
    user_id = "user-1"
    connection.execute(
        "INSERT INTO users(id, created_at) VALUES (?, ?)",
        (user_id, NOW.isoformat()),
    )
    connection.execute(
        """INSERT INTO auth_identities(
               id, user_id, provider, provider_subject, password_hash,
               verified_at, created_at
           ) VALUES (?, ?, 'email', ?, ?, ?, ?)""",
        (
            "identity-1",
            user_id,
            email,
            "scrypt$test",
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.commit()
    return user_id


def test_create_and_load_invitation_persists_only_digest(
    connection: sqlite3.Connection,
) -> None:
    repository = ParticipantInvitationsRepository(connection)
    invitation = _invitation()

    assert repository.create_for_owner(invitation, owner_id="owner-1", event_id=EVENT_ID, now=NOW)
    loaded = repository.get_by_token_hash("digest-1")

    assert loaded == invitation
    row = connection.execute(
        "SELECT token_hash FROM participant_invitations WHERE id = ?",
        (str(invitation.id),),
    ).fetchone()
    assert row["token_hash"] == "digest-1"


def test_non_owner_cannot_create_or_revoke_invitation(
    connection: sqlite3.Connection,
) -> None:
    repository = ParticipantInvitationsRepository(connection)
    invitation = _invitation()

    assert not repository.create_for_owner(
        invitation, owner_id="other-owner", now=NOW
    )
    assert repository.create_for_owner(invitation, owner_id="owner-1", now=NOW)
    assert not repository.revoke_for_owner(
        invitation_id=invitation.id, event_id=EVENT_ID, owner_id="other-owner", now=NOW
    )
    assert repository.get_by_token_hash("digest-1").revoked_at is None


def test_new_invitation_revokes_previous_pending_invitation(
    connection: sqlite3.Connection,
) -> None:
    repository = ParticipantInvitationsRepository(connection)
    first = _invitation(token_hash="digest-1")
    second = _invitation(token_hash="digest-2", now=NOW + timedelta(minutes=1))

    assert repository.create_for_owner(first, owner_id="owner-1", event_id=EVENT_ID, now=NOW)
    assert repository.create_for_owner(
        second, owner_id="owner-1", event_id=EVENT_ID, now=NOW + timedelta(minutes=1)
    )

    assert repository.get_by_token_hash("digest-1").revoked_at == NOW + timedelta(minutes=1)
    assert repository.get_by_token_hash("digest-2").revoked_at is None


def test_acceptance_requires_matching_verified_email_and_is_single_use(
    connection: sqlite3.Connection,
) -> None:
    repository = ParticipantInvitationsRepository(connection)
    invitation = _invitation()
    assert repository.create_for_owner(invitation, owner_id="owner-1", now=NOW)
    user_id = _verified_user(connection, email="ALICE@example.test")

    assert repository.accept_for_verified_user(
        token_hash="digest-1", user_id=user_id, now=NOW + timedelta(minutes=1)
    )
    assert not repository.accept_for_verified_user(
        token_hash="digest-1", user_id=user_id, now=NOW + timedelta(minutes=2)
    )
    linked_user = connection.execute(
        "SELECT linked_user_id FROM event_participants WHERE id = ?",
        (str(PARTICIPANT_ID),),
    ).fetchone()["linked_user_id"]
    accepted_at = repository.get_by_token_hash("digest-1").accepted_at

    assert linked_user == user_id
    assert accepted_at == NOW + timedelta(minutes=1)


def test_acceptance_rejects_unverified_or_mismatched_account(
    connection: sqlite3.Connection,
) -> None:
    repository = ParticipantInvitationsRepository(connection)
    invitation = _invitation()
    assert repository.create_for_owner(invitation, owner_id="owner-1", now=NOW)
    user_id = _verified_user(connection, email="bob@example.test")

    assert not repository.accept_for_verified_user(
        token_hash="digest-1", user_id=user_id, now=NOW + timedelta(minutes=1)
    )
    assert connection.execute(
        "SELECT linked_user_id FROM event_participants WHERE id = ?",
        (str(PARTICIPANT_ID),),
    ).fetchone()["linked_user_id"] is None
    assert repository.get_by_token_hash("digest-1").accepted_at is None


def test_expired_or_revoked_invitation_cannot_be_accepted(
    connection: sqlite3.Connection,
) -> None:
    repository = ParticipantInvitationsRepository(connection)
    invitation = _invitation()
    assert repository.create_for_owner(invitation, owner_id="owner-1", now=NOW)
    user_id = _verified_user(connection, email="alice@example.test")

    assert not repository.accept_for_verified_user(
        token_hash="digest-1",
        user_id=user_id,
        now=invitation.expires_at,
    )
    assert repository.revoke_for_owner(
        invitation_id=invitation.id, event_id=EVENT_ID, owner_id="owner-1", now=NOW + timedelta(minutes=1)
    )
    assert not repository.accept_for_verified_user(
        token_hash="digest-1",
        user_id=user_id,
        now=NOW + timedelta(minutes=2),
    )


def test_failed_participant_link_rolls_back_invitation_acceptance(
    connection: sqlite3.Connection,
) -> None:
    repository = ParticipantInvitationsRepository(connection)
    invitation = _invitation()
    assert repository.create_for_owner(invitation, owner_id="owner-1", now=NOW)
    user_id = _verified_user(connection, email="alice@example.test")
    connection.execute(
        """CREATE TRIGGER reject_participant_link
           BEFORE UPDATE OF linked_user_id ON event_participants
           BEGIN SELECT RAISE(ABORT, 'link rejected'); END"""
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="link rejected"):
        repository.accept_for_verified_user(
            token_hash="digest-1", user_id=user_id, now=NOW + timedelta(minutes=1)
        )

    assert repository.get_by_token_hash("digest-1").accepted_at is None
    assert connection.execute(
        "SELECT linked_user_id FROM event_participants WHERE id = ?",
        (str(PARTICIPANT_ID),),
    ).fetchone()["linked_user_id"] is None

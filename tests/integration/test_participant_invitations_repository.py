from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import sqlite3
from threading import Barrier
from uuid import UUID

import pytest

from app.domain.participant_invitations import ParticipantInvitation
from app.infrastructure.database.connection import connect, migrate
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
        invitation, owner_id="other-owner", event_id=EVENT_ID, now=NOW
    )
    assert repository.create_for_owner(invitation, owner_id="owner-1", event_id=EVENT_ID, now=NOW)
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
    assert repository.create_for_owner(invitation, owner_id="owner-1", event_id=EVENT_ID, now=NOW)
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
    assert repository.create_for_owner(invitation, owner_id="owner-1", event_id=EVENT_ID, now=NOW)
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
    assert repository.create_for_owner(invitation, owner_id="owner-1", event_id=EVENT_ID, now=NOW)
    user_id = _verified_user(connection, email="alice@example.test")

    assert not repository.accept_for_verified_user(
        token_hash="digest-1",
        user_id=user_id,
        now=invitation.expires_at,
    )
    assert repository.revoke_for_owner(
        invitation_id=invitation.id,
        event_id=EVENT_ID,
        owner_id="owner-1",
        now=NOW + timedelta(minutes=1),
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
    assert repository.create_for_owner(invitation, owner_id="owner-1", event_id=EVENT_ID, now=NOW)
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



def test_concurrent_acceptance_consumes_invitation_exactly_once(tmp_path) -> None:
    database_path = tmp_path / "concurrent-invitations.sqlite3"
    first_connection = connect(database_path)
    migrate(first_connection)
    first_connection.execute(
        """INSERT INTO events(id, owner_id, title, starts_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (str(EVENT_ID), "owner-1", "Game night", None, NOW.isoformat()),
    )
    first_connection.execute(
        """INSERT INTO event_participants(
               id, event_id, display_name, added_at, linked_user_id
           ) VALUES (?, ?, ?, ?, NULL)""",
        (str(PARTICIPANT_ID), str(EVENT_ID), "Alice", NOW.isoformat()),
    )
    first_connection.commit()
    invitation = _invitation()
    first_repository = ParticipantInvitationsRepository(first_connection)
    assert first_repository.create_for_owner(
        invitation, owner_id="owner-1", event_id=EVENT_ID, now=NOW
    )
    user_id = _verified_user(first_connection, email="alice@example.test")
    second_connection = connect(database_path)
    barrier = Barrier(2)

    def accept(connection: sqlite3.Connection) -> bool:
        try:
            barrier.wait(timeout=5)
            return ParticipantInvitationsRepository(connection).accept_for_verified_user(
                token_hash="digest-1",
                user_id=user_id,
                now=NOW + timedelta(minutes=1),
            )
        finally:
            connection.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(accept, (first_connection, second_connection))
            )
        assert sorted(outcomes) == [False, True]
        verification_connection = connect(database_path)
        try:
            verification_repository = ParticipantInvitationsRepository(
                verification_connection
            )
            assert verification_repository.get_by_token_hash(
                "digest-1"
            ).accepted_at == NOW + timedelta(minutes=1)
            linked_user = verification_connection.execute(
                "SELECT linked_user_id FROM event_participants WHERE id = ?",
                (str(PARTICIPANT_ID),),
            ).fetchone()["linked_user_id"]
            assert linked_user == user_id
        finally:
            verification_connection.close()
    finally:
        first_connection.close()



def test_cannot_create_invitation_after_participant_is_linked(
    connection: sqlite3.Connection,
) -> None:
    repository = ParticipantInvitationsRepository(connection)
    user_id = _verified_user(connection, email="alice@example.test")
    connection.execute(
        "UPDATE event_participants SET linked_user_id = ? WHERE id = ?",
        (user_id, str(PARTICIPANT_ID)),
    )
    connection.commit()

    assert not repository.create_for_owner(
        _invitation(),
        owner_id="owner-1",
        event_id=EVENT_ID,
        now=NOW,
    )
    assert connection.execute(
        "SELECT COUNT(*) FROM participant_invitations"
    ).fetchone()[0] == 0



def test_concurrent_acceptance_cannot_link_one_participant_to_two_accounts(
    tmp_path,
) -> None:
    database_path = tmp_path / "competing-invitation-acceptance.sqlite3"
    setup_connection = connect(database_path)
    migrate(setup_connection)
    setup_connection.execute(
        """INSERT INTO events(id, owner_id, title, starts_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (str(EVENT_ID), "owner-1", "Game night", None, NOW.isoformat()),
    )
    setup_connection.execute(
        """INSERT INTO event_participants(
               id, event_id, display_name, added_at, linked_user_id
           ) VALUES (?, ?, ?, ?, NULL)""",
        (str(PARTICIPANT_ID), str(EVENT_ID), "Alice", NOW.isoformat()),
    )
    setup_connection.commit()

    invitation = _invitation()
    assert ParticipantInvitationsRepository(setup_connection).create_for_owner(
        invitation, owner_id="owner-1", event_id=EVENT_ID, now=NOW
    )

    # Both accounts have verified addresses that normalize to the invitation
    # email. They race with the same bearer token on independent connections.
    competing_user_ids = ("user-1", "user-2")
    for user_id, identity_id, email in (
        ("user-1", "identity-1", "Alice@example.test"),
        ("user-2", "identity-2", "alice@example.test"),
    ):
        setup_connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            (user_id, NOW.isoformat()),
        )
        setup_connection.execute(
            """INSERT INTO auth_identities(
                   id, user_id, provider, provider_subject, password_hash,
                   verified_at, created_at
               ) VALUES (?, ?, 'email', ?, ?, ?, ?)""",
            (
                identity_id,
                user_id,
                email,
                "scrypt$test",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    setup_connection.commit()

    connections = (setup_connection, connect(database_path))
    barrier = Barrier(2)

    def accept(connection: sqlite3.Connection, user_id: str) -> tuple[str, bool]:
        try:
            barrier.wait(timeout=5)
            accepted = ParticipantInvitationsRepository(
                connection
            ).accept_for_verified_user(
                token_hash="digest-1",
                user_id=user_id,
                now=NOW + timedelta(minutes=1),
            )
            return user_id, accepted
        finally:
            connection.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    lambda args: accept(*args),
                    zip(connections, competing_user_ids),
                )
            )

        assert sorted(accepted for _, accepted in outcomes) == [False, True]

        verification_connection = connect(database_path)
        try:
            repository = ParticipantInvitationsRepository(verification_connection)
            assert repository.get_by_token_hash("digest-1").accepted_at == (
                NOW + timedelta(minutes=1)
            )
            linked_user_id = verification_connection.execute(
                "SELECT linked_user_id FROM event_participants WHERE id = ?",
                (str(PARTICIPANT_ID),),
            ).fetchone()["linked_user_id"]
            assert linked_user_id in competing_user_ids
            assert linked_user_id == next(
                user_id for user_id, accepted in outcomes if accepted
            )
        finally:
            verification_connection.close()
    finally:
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass

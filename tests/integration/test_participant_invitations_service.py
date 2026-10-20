from datetime import UTC, datetime
import sqlite3
from uuid import UUID

import pytest

from app.application.events import EventNotFoundError, EventsService
from app.application.participant_invitations import ParticipantInvitationsService
from app.infrastructure.database.connection import migrate
from app.infrastructure.database.events_repository import EventsRepository
from app.infrastructure.database.participant_invitations_repository import (
    ParticipantInvitationsRepository,
)
from app.infrastructure.database.participants_repository import ParticipantsRepository


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
EVENT_ID = UUID("00000000-0000-0000-0000-000000000010")
PARTICIPANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def setup():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    migrate(db)
    db.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) "
        "VALUES (?, ?, ?, NULL, ?)",
        (str(EVENT_ID), "owner-1", "Game night", NOW.isoformat()),
    )
    db.execute(
        "INSERT INTO event_participants(id, event_id, display_name, added_at, linked_user_id) "
        "VALUES (?, ?, ?, ?, NULL)",
        (str(PARTICIPANT_ID), str(EVENT_ID), "Alice", NOW.isoformat()),
    )
    db.commit()
    events = EventsService(EventsRepository(db))
    participants = ParticipantsRepository(db)
    invitations = ParticipantInvitationsRepository(db)
    service = ParticipantInvitationsService(
        events=events, participants=participants, invitations=invitations
    )
    yield db, service, invitations
    db.close()


def test_service_returns_raw_token_once_and_stores_only_digest(setup) -> None:
    db, service, repository = setup
    created = service.create_invitation(
        event_id=EVENT_ID,
        participant_id=PARTICIPANT_ID,
        owner_id="owner-1",
        target_email=" Alice@Example.test ",
        now=NOW,
    )

    assert created.raw_token
    assert created.invitation.target_email == "alice@example.test"
    assert created.invitation.token_hash != created.raw_token
    assert repository.get_by_token_hash(created.invitation.token_hash) == created.invitation
    rows = db.execute("SELECT token_hash FROM participant_invitations").fetchall()
    assert len(rows) == 1
    assert rows[0]["token_hash"] == created.invitation.token_hash
    assert created.raw_token not in rows[0]["token_hash"]


def test_service_rejects_non_owner_and_participant_from_another_event(setup) -> None:
    db, service, _ = setup
    with pytest.raises(EventNotFoundError):
        service.create_invitation(
            event_id=EVENT_ID,
            participant_id=PARTICIPANT_ID,
            owner_id="someone-else",
            target_email="alice@example.test",
            now=NOW,
        )

    other_event_id = UUID("00000000-0000-0000-0000-000000000011")
    db.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) "
        "VALUES (?, ?, ?, NULL, ?)",
        (str(other_event_id), "owner-1", "Other event", NOW.isoformat()),
    )
    db.commit()
    with pytest.raises(LookupError, match="Participant not found"):
        service.create_invitation(
            event_id=other_event_id,
            participant_id=PARTICIPANT_ID,
            owner_id="owner-1",
            target_email="alice@example.test",
            now=NOW,
        )


def test_acceptance_uses_authenticated_user_and_rejects_bad_token_generically(setup) -> None:
    db, service, _ = setup
    created = service.create_invitation(
        event_id=EVENT_ID,
        participant_id=PARTICIPANT_ID,
        owner_id="owner-1",
        target_email="alice@example.test",
        now=NOW,
    )
    assert not service.accept_invitation(
        raw_token="not-the-token", authenticated_user_id="user-1", now=NOW
    )

    db.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("user-1", NOW.isoformat()))
    db.execute(
        """INSERT INTO auth_identities(
               id, user_id, provider, provider_subject, password_hash,
               verified_at, created_at
           ) VALUES (?, ?, 'email', ?, ?, ?, ?)""",
        (
            "identity-1",
            "user-1",
            "ALICE@example.test",
            "scrypt$test",
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    db.commit()
    assert service.accept_invitation(
        raw_token=created.raw_token, authenticated_user_id="user-1", now=NOW
    )
    linked = db.execute(
        "SELECT linked_user_id FROM event_participants WHERE id = ?",
        (str(PARTICIPANT_ID),),
    ).fetchone()["linked_user_id"]
    assert linked == "user-1"


def test_revoke_invitation_is_scoped_to_event(setup) -> None:
    _, service, repository = setup
    created = service.create_invitation(
        event_id=EVENT_ID,
        participant_id=PARTICIPANT_ID,
        owner_id="owner-1",
        target_email="alice@example.test",
        now=NOW,
    )
    other_event_id = UUID("00000000-0000-0000-0000-000000000011")
    assert not repository.revoke_for_owner(
        invitation_id=created.invitation.id,
        event_id=other_event_id,
        owner_id="owner-1",
        now=NOW,
    )
    assert service.revoke_invitation(
        event_id=EVENT_ID,
        invitation_id=created.invitation.id,
        owner_id="owner-1",
        now=NOW,
    )

import sqlite3
from datetime import UTC
from uuid import uuid4

import pytest

from app.application.events import EventNotFoundError, EventsService
from app.application.participants import DuplicateParticipantError, ParticipantsService
from app.infrastructure.database.connection import migrate
from app.infrastructure.database.events_repository import EventsRepository
from app.infrastructure.database.participants_repository import ParticipantsRepository


@pytest.fixture
def services() -> tuple[EventsService, ParticipantsService]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    events = EventsService(EventsRepository(connection))
    participants = ParticipantsService(events, ParticipantsRepository(connection))
    yield events, participants
    connection.close()


def test_owner_can_add_and_list_participants(
    services: tuple[EventsService, ParticipantsService],
) -> None:
    events, participants = services
    event = events.create_event(owner_id="alice", title="Game night")
    created = participants.add_participant(
        event_id=event.id, owner_id="alice", display_name=" Bob "
    )
    listed = participants.list_participants(event_id=event.id, owner_id="alice")
    assert created.display_name == "Bob"
    assert [person.id for person in listed] == [created.id]
    assert listed[0].added_at.tzinfo == UTC


def test_other_user_cannot_add_participant(
    services: tuple[EventsService, ParticipantsService],
) -> None:
    events, participants = services
    event = events.create_event(owner_id="alice", title="Private event")
    with pytest.raises(EventNotFoundError):
        participants.add_participant(
            event_id=event.id, owner_id="bob", display_name="Mallory"
        )


def test_other_user_cannot_list_participants(
    services: tuple[EventsService, ParticipantsService],
) -> None:
    events, participants = services
    event = events.create_event(owner_id="alice", title="Private event")
    participants.add_participant(event_id=event.id, owner_id="alice", display_name="Bob")
    with pytest.raises(EventNotFoundError):
        participants.list_participants(event_id=event.id, owner_id="bob")


def test_duplicate_participant_name_has_application_error(
    services: tuple[EventsService, ParticipantsService],
) -> None:
    events, participants = services
    event = events.create_event(owner_id="alice", title="Game night")
    participants.add_participant(event_id=event.id, owner_id="alice", display_name="Bob")
    with pytest.raises(DuplicateParticipantError, match="already exists"):
        participants.add_participant(
            event_id=event.id, owner_id="alice", display_name=" Bob "
        )


def test_participant_can_be_linked_to_an_existing_account() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    try:
        events = EventsService(EventsRepository(connection))
        repository = ParticipantsRepository(connection)
        participants = ParticipantsService(events, repository)
        event = events.create_event(owner_id="alice", title="Game night")
        guest = participants.add_participant(
            event_id=event.id, owner_id="alice", display_name="Bob"
        )
        user_id = uuid4()
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            (str(user_id), "2026-10-20T00:00:00Z"),
        )

        assert guest.linked_user_id is None
        assert repository.link_to_user(
            participant_id=guest.id, event_id=event.id, user_id=user_id
        )
        linked = participants.list_participants(
            event_id=event.id, owner_id="alice"
        )[0]
        assert linked.id == guest.id
        assert linked.linked_user_id == user_id
        assert not repository.link_to_user(
            participant_id=guest.id, event_id=event.id, user_id=user_id
        )
    finally:
        connection.close()

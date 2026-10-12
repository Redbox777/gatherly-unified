import sqlite3

import pytest

from app.application.events import EventNotFoundError, EventsService
from app.application.participants import DuplicateParticipantError, ParticipantsService
from app.infrastructure.database.connection import migrate
from app.infrastructure.database.events_repository import EventsRepository
from app.infrastructure.database.participants_repository import ParticipantsRepository


@pytest.fixture
def services():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    events = EventsService(EventsRepository(connection))
    participants = ParticipantsService(events, ParticipantsRepository(connection))
    yield events, participants
    connection.close()


def test_owner_can_add_and_list_participants(services) -> None:
    events, participants = services
    event = events.create_event(owner_id="alice", title="Game night")
    created = participants.add_participant(
        event_id=event.id, owner_id="alice", display_name=" Bob "
    )
    listed = participants.list_participants(event_id=event.id, owner_id="alice")
    assert created.display_name == "Bob"
    assert [person.id for person in listed] == [created.id]


def test_other_user_cannot_add_participant(services) -> None:
    events, participants = services
    event = events.create_event(owner_id="alice", title="Private event")
    with pytest.raises(EventNotFoundError):
        participants.add_participant(
            event_id=event.id, owner_id="bob", display_name="Mallory"
        )


def test_other_user_cannot_list_participants(services) -> None:
    events, participants = services
    event = events.create_event(owner_id="alice", title="Private event")
    participants.add_participant(event_id=event.id, owner_id="alice", display_name="Bob")
    with pytest.raises(EventNotFoundError):
        participants.list_participants(event_id=event.id, owner_id="bob")


def test_duplicate_participant_name_has_application_error(services) -> None:
    events, participants = services
    event = events.create_event(owner_id="alice", title="Game night")
    participants.add_participant(event_id=event.id, owner_id="alice", display_name="Bob")
    with pytest.raises(DuplicateParticipantError, match="already exists"):
        participants.add_participant(event_id=event.id, owner_id="alice", display_name="Bob")


def test_unrelated_integrity_errors_are_not_misreported(services) -> None:
    events, participants = services
    event = events.create_event(owner_id="alice", title="Game night")
    participants.add_participant(event_id=event.id, owner_id="alice", display_name="Bob")
    with pytest.raises(sqlite3.IntegrityError):
        participants.add_participant(event_id=event.id, owner_id="alice", display_name="Bob")

from uuid import uuid4

import pytest

from app.application.events import EventNotFoundError, EventsService
from app.application.participants import ParticipantsService
from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.events_repository import EventsRepository
from app.infrastructure.database.participants_repository import ParticipantsRepository


def test_cannot_add_or_list_participants_for_missing_event(tmp_path) -> None:
    connection = connect(tmp_path / "gatherly.sqlite3")
    try:
        migrate(connection)
        events = EventsService(EventsRepository(connection))
        participants = ParticipantsService(events, ParticipantsRepository(connection))
        missing_event_id = uuid4()

        with pytest.raises(EventNotFoundError, match="Event not found"):
            participants.add_participant(
                event_id=missing_event_id,
                owner_id="alice",
                display_name="Bob",
            )

        with pytest.raises(EventNotFoundError, match="Event not found"):
            participants.list_participants(
                event_id=missing_event_id,
                owner_id="alice",
            )

        count = connection.execute(
            "SELECT COUNT(*) FROM event_participants"
        ).fetchone()[0]
        assert count == 0
    finally:
        connection.close()

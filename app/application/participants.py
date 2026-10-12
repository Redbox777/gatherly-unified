"""Use cases for adding and listing event participants."""

import sqlite3
from uuid import UUID

from app.application.events import EventsService
from app.domain.participants import EventParticipant
from app.infrastructure.database.connection import transaction
from app.infrastructure.database.participants_repository import ParticipantsRepository


class DuplicateParticipantError(ValueError):
    """Raised when an event already has a participant with the same display name."""


class ParticipantsService:
    def __init__(
        self, events: EventsService, repository: ParticipantsRepository
    ) -> None:
        self._events = events
        self._repository = repository

    def add_participant(
        self, *, event_id: UUID, owner_id: str, display_name: str
    ) -> EventParticipant:
        self._events.get_event(event_id=event_id, owner_id=owner_id)
        participant = EventParticipant.create(
            event_id=event_id, display_name=display_name
        )
        try:
            with transaction(self._repository.connection):
                self._repository.add(participant)
        except sqlite3.IntegrityError as exc:
            if (
                str(exc)
                == "UNIQUE constraint failed: event_participants.event_id, "
                "event_participants.display_name"
            ):
                raise DuplicateParticipantError(
                    "A participant with this name already exists for the event"
                ) from exc
            raise
        return participant

    def list_participants(
        self, *, event_id: UUID, owner_id: str
    ) -> list[EventParticipant]:
        self._events.get_event(event_id=event_id, owner_id=owner_id)
        return self._repository.list_for_event(event_id)

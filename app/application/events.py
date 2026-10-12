"""Use cases for event creation and retrieval."""

from datetime import datetime
from uuid import UUID

from app.domain.events import Event
from app.infrastructure.database.events_repository import EventsRepository


class EventNotFoundError(LookupError):
    """Raised when an event is missing or does not belong to the requester."""


class EventsService:
    def __init__(self, repository: EventsRepository) -> None:
        self._repository = repository

    def create_event(
        self, *, owner_id: str, title: str, starts_at: datetime | None = None
    ) -> Event:
        event = Event.create(owner_id=owner_id, title=title, starts_at=starts_at)
        self._repository.save(event)
        return event

    def get_event(self, *, event_id: UUID, owner_id: str) -> Event:
        event = self._repository.get_for_owner(event_id=event_id, owner_id=owner_id)
        if event is None:
            # Deliberately do not reveal whether another user's event exists.
            raise EventNotFoundError("Event not found")
        return event

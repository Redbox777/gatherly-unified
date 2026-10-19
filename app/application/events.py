"""Use cases for event creation, retrieval, listing, updates, and deletion."""

from datetime import datetime
from uuid import UUID

from app.domain.events import Event
from app.infrastructure.database.connection import transaction
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
        with transaction(self._repository.connection):
            self._repository.save(event)
        return event

    def get_event(self, *, event_id: UUID, owner_id: str) -> Event:
        event = self._repository.get_for_owner(event_id=event_id, owner_id=owner_id)
        if event is None:
            # Deliberately do not reveal whether another user's event exists.
            raise EventNotFoundError("Event not found")
        return event

    def list_events(self, *, owner_id: str) -> list[Event]:
        return self._repository.list_for_owner(owner_id=owner_id)

    def update_event(
        self,
        *,
        event_id: UUID,
        owner_id: str,
        title: str,
        starts_at: datetime | None = None,
    ) -> Event:
        existing = self.get_event(event_id=event_id, owner_id=owner_id)
        updated = Event(
            id=existing.id,
            owner_id=existing.owner_id,
            title=Event.create(
                owner_id=owner_id, title=title, starts_at=starts_at
            ).title,
            starts_at=starts_at.astimezone() if starts_at and starts_at.tzinfo else starts_at,
            created_at=existing.created_at,
        )
        with transaction(self._repository.connection):
            changed = self._repository.update_for_owner(
                event_id=event_id,
                owner_id=owner_id,
                title=updated.title,
                starts_at=updated.starts_at,
            )
            if not changed:
                raise EventNotFoundError("Event not found")
        return updated

    def delete_event(self, *, event_id: UUID, owner_id: str) -> None:
        with transaction(self._repository.connection):
            deleted = self._repository.delete_for_owner(
                event_id=event_id, owner_id=owner_id
            )
            if not deleted:
                raise EventNotFoundError("Event not found")

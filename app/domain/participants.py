"""Event participant value object."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class EventParticipant:
    id: UUID
    event_id: UUID
    display_name: str
    added_at: datetime

    @classmethod
    def create(cls, *, event_id: UUID, display_name: str) -> "EventParticipant":
        name = display_name.strip()
        if not name:
            raise ValueError("display_name must not be empty")
        if len(name) > 120:
            raise ValueError("display_name must be 120 characters or fewer")
        return cls(
            id=uuid4(),
            event_id=event_id,
            display_name=name,
            added_at=datetime.now(UTC),
        )

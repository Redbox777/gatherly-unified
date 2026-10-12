"""Event domain model and validation."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class Event:
    id: UUID
    owner_id: str
    title: str
    starts_at: datetime | None
    created_at: datetime

    @classmethod
    def create(
        cls, *, owner_id: str, title: str, starts_at: datetime | None = None
    ) -> "Event":
        normalized_owner = owner_id.strip()
        normalized_title = title.strip()
        if not normalized_owner:
            raise ValueError("owner_id must not be empty")
        if not normalized_title:
            raise ValueError("title must not be empty")
        if len(normalized_title) > 200:
            raise ValueError("title must be 200 characters or fewer")
        if starts_at is not None and starts_at.tzinfo is None:
            raise ValueError("starts_at must be timezone-aware")
        if starts_at is not None:
            starts_at = starts_at.astimezone(timezone.utc)
        return cls(
            id=uuid4(),
            owner_id=normalized_owner,
            title=normalized_title,
            starts_at=starts_at,
            created_at=datetime.now(timezone.utc),
        )

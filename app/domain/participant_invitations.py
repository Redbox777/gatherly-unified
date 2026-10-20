"""Domain model for participant invitations."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4


INVITATION_LIFETIME = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class ParticipantInvitation:
    id: UUID
    participant_id: UUID
    target_email: str
    token_hash: str
    created_by_owner_id: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        participant_id: UUID,
        target_email: str,
        token_hash: str,
        created_by_owner_id: str,
        now: datetime | None = None,
    ) -> "ParticipantInvitation":
        email = target_email.strip().casefold()
        owner_id = created_by_owner_id.strip()
        normalized_hash = token_hash.strip()
        if not email or "@" not in email:
            raise ValueError("target_email must be a valid email address")
        if not owner_id:
            raise ValueError("created_by_owner_id must not be empty")
        if not normalized_hash:
            raise ValueError("token_hash must not be empty")
        created_at = now or datetime.now(UTC)
        if created_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        created_at = created_at.astimezone(UTC)
        return cls(
            id=uuid4(),
            participant_id=participant_id,
            target_email=email,
            token_hash=normalized_hash,
            created_by_owner_id=owner_id,
            created_at=created_at,
            expires_at=created_at + INVITATION_LIFETIME,
        )

    def is_usable(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        current = current.astimezone(UTC)
        return (
            current < self.expires_at
            and self.accepted_at is None
            and self.revoked_at is None
        )

"""Application use cases for creating, revoking, and accepting participant invitations."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.application.events import EventsService
from app.domain.auth_tokens import hash_token, issue_token
from app.domain.participant_invitations import ParticipantInvitation
from app.infrastructure.database.participant_invitations_repository import (
    ParticipantInvitationsRepository,
)
from app.infrastructure.database.participants_repository import ParticipantsRepository


@dataclass(frozen=True, slots=True)
class CreatedParticipantInvitation:
    """An invitation plus its one-time raw token for a delivery adapter."""

    invitation: ParticipantInvitation
    raw_token: str


class ParticipantInvitationsService:
    """Enforce event ownership and participant scope around invitation persistence."""

    def __init__(
        self,
        *,
        events: EventsService,
        participants: ParticipantsRepository,
        invitations: ParticipantInvitationsRepository,
    ) -> None:
        self._events = events
        self._participants = participants
        self._invitations = invitations

    def create_invitation(
        self,
        *,
        event_id: UUID,
        participant_id: UUID,
        owner_id: str,
        target_email: str,
        now: datetime,
    ) -> CreatedParticipantInvitation:
        self._events.get_event(event_id=event_id, owner_id=owner_id)
        participant_ids = {
            participant.id for participant in self._participants.list_for_event(event_id)
        }
        if participant_id not in participant_ids:
            raise LookupError("Participant not found")

        issued = issue_token()
        invitation = ParticipantInvitation.create(
            participant_id=participant_id,
            target_email=target_email,
            token_hash=issued.digest,
            created_by_owner_id=owner_id,
            now=now,
        )
        created = self._invitations.create_for_owner(
            invitation,
            owner_id=owner_id,
            event_id=event_id,
            now=now,
        )
        if not created:
            raise LookupError("Participant not found")
        return CreatedParticipantInvitation(invitation=invitation, raw_token=issued.raw)

    def revoke_invitation(
        self,
        *,
        event_id: UUID,
        invitation_id: UUID,
        owner_id: str,
        now: datetime,
    ) -> bool:
        self._events.get_event(event_id=event_id, owner_id=owner_id)
        return self._invitations.revoke_for_owner(
            invitation_id=invitation_id,
            event_id=event_id,
            owner_id=owner_id,
            now=now,
        )

    def accept_invitation(
        self, *, raw_token: str, authenticated_user_id: str, now: datetime
    ) -> bool:
        """Return one generic result for malformed, invalid, expired, or used tokens."""
        try:
            digest = hash_token(raw_token)
        except ValueError:
            return False
        return self._invitations.accept_for_verified_user(
            token_hash=digest,
            user_id=authenticated_user_id,
            now=now,
        )

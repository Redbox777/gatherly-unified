from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.participant_invitations import (
    INVITATION_LIFETIME,
    ParticipantInvitation,
)


def test_invitation_normalizes_email_and_expires_after_seven_days() -> None:
    created_at = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    invitation = ParticipantInvitation.create(
        participant_id=uuid4(),
        target_email="  Alice@Example.COM ",
        token_hash="token-hash",
        created_by_owner_id=" alice ",
        now=created_at,
    )

    assert invitation.target_email == "alice@example.com"
    assert invitation.created_by_owner_id == "alice"
    assert invitation.created_at == created_at
    assert invitation.expires_at == created_at + timedelta(days=7)
    assert INVITATION_LIFETIME == timedelta(days=7)


def test_invitation_is_usable_only_before_expiry_and_before_consumption() -> None:
    created_at = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    invitation = ParticipantInvitation.create(
        participant_id=uuid4(),
        target_email="alice@example.com",
        token_hash="token-hash",
        created_by_owner_id="alice",
        now=created_at,
    )

    assert invitation.is_usable(now=created_at)
    assert invitation.is_usable(now=invitation.expires_at - timedelta(microseconds=1))
    assert not invitation.is_usable(now=invitation.expires_at)
    assert not replace(invitation, accepted_at=created_at).is_usable(now=created_at)
    assert not replace(invitation, revoked_at=created_at).is_usable(now=created_at)


@pytest.mark.parametrize(
    ("target_email", "token_hash", "owner_id"),
    [
        ("", "token-hash", "alice"),
        ("not-an-email", "token-hash", "alice"),
        ("alice@example.com", "", "alice"),
        ("alice@example.com", "token-hash", " "),
    ],
)
def test_invitation_rejects_invalid_required_fields(
    target_email: str, token_hash: str, owner_id: str
) -> None:
    with pytest.raises(ValueError):
        ParticipantInvitation.create(
            participant_id=uuid4(),
            target_email=target_email,
            token_hash=token_hash,
            created_by_owner_id=owner_id,
            now=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
        )


def test_invitation_requires_timezone_aware_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ParticipantInvitation.create(
            participant_id=uuid4(),
            target_email="alice@example.com",
            token_hash="token-hash",
            created_by_owner_id="alice",
            now=datetime(2026, 10, 20, 12, 0),
        )

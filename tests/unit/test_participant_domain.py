from uuid import uuid4

import pytest

from app.domain.participants import EventParticipant


def test_participant_normalizes_display_name() -> None:
    participant = EventParticipant.create(
        event_id=uuid4(),
        display_name="  Alice  ",
    )
    assert participant.display_name == "Alice"


def test_participant_rejects_empty_display_name() -> None:
    with pytest.raises(ValueError, match="display_name"):
        EventParticipant.create(event_id=uuid4(), display_name="   ")


def test_participant_rejects_name_over_120_characters() -> None:
    with pytest.raises(ValueError, match="120 characters"):
        EventParticipant.create(event_id=uuid4(), display_name="A" * 121)

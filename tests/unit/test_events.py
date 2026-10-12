from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.events import Event


def test_event_normalizes_title_and_owner() -> None:
    event = Event.create(owner_id=" alice ", title="  Birthday  ")
    assert event.owner_id == "alice"
    assert event.title == "Birthday"
    assert event.starts_at is None


def test_event_rejects_empty_title() -> None:
    with pytest.raises(ValueError, match="title"):
        Event.create(owner_id="alice", title="  ")


def test_event_rejects_naive_start_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Event.create(owner_id="alice", title="Meeting", starts_at=datetime(2026, 1, 1))


def test_event_converts_start_time_to_utc() -> None:
    local = datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=3)))
    event = Event.create(owner_id="alice", title="Meeting", starts_at=local)
    assert event.starts_at == datetime(2026, 1, 1, 9, tzinfo=UTC)

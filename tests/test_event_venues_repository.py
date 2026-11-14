"""Persistence and authorization tests for event venues."""

from datetime import UTC, datetime

import pytest

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.event_venues_repository import EventVenuesRepository


@pytest.fixture
def repository(tmp_path):
    connection = connect(tmp_path / "venues.sqlite3")
    migrate(connection)
    now = datetime.now(UTC).isoformat()
    connection.execute(
        "INSERT INTO users(id, created_at) VALUES (?, ?)", ("owner", now)
    )
    connection.execute(
        "INSERT INTO users(id, created_at) VALUES (?, ?)", ("member", now)
    )
    connection.execute(
        "INSERT INTO users(id, created_at) VALUES (?, ?)", ("outsider", now)
    )
    connection.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("event-1", "owner", "Planning", None, now),
    )
    connection.execute(
        "INSERT INTO event_participants(id, event_id, display_name, added_at, linked_user_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("participant-1", "event-1", "Member", now, "member"),
    )
    connection.commit()
    yield EventVenuesRepository(connection)
    connection.close()


def test_owner_can_save_read_update_and_delete_venue(repository):
    venue = repository.save_for_owner(
        event_id="event-1", owner_id="owner", venue_name="City Park",
        address="Main Street 1", latitude=50.1, longitude=6.1,
    )
    assert venue == {
        "venue_name": "City Park", "address": "Main Street 1",
        "latitude": 50.1, "longitude": 6.1,
    }
    updated = repository.save_for_owner(
        event_id="event-1", owner_id="owner", venue_name="Town Hall",
        address="Market Square 2", latitude=None, longitude=None,
    )
    assert updated is not None
    assert updated["venue_name"] == "Town Hall"
    assert updated["latitude"] is None
    assert repository.delete_for_owner(event_id="event-1", owner_id="owner")
    assert repository.get_for_owner(event_id="event-1", owner_id="owner") is None



def test_linked_member_can_read_venue_but_cannot_change_it(repository):
    repository.save_for_owner(
        event_id="event-1", owner_id="owner", venue_name="City Park",
        address="Main Street 1", latitude=50.1, longitude=6.1,
    )
    assert repository.is_member(event_id="event-1", user_id="member")
    assert repository.get_for_member(event_id="event-1", user_id="member") == {
        "venue_name": "City Park",
        "address": "Main Street 1",
        "latitude": 50.1,
        "longitude": 6.1,
    }
    assert repository.save_for_owner(
        event_id="event-1", owner_id="member", venue_name="Changed",
        address="Other Street", latitude=None, longitude=None,
    ) is None
    assert not repository.delete_for_owner(event_id="event-1", owner_id="member")


def test_unlinked_user_cannot_read_event_venue(repository):
    repository.save_for_owner(
        event_id="event-1", owner_id="owner", venue_name="City Park",
        address="Main Street 1", latitude=None, longitude=None,
    )
    assert not repository.is_member(event_id="event-1", user_id="outsider")
    assert repository.get_for_member(event_id="event-1", user_id="outsider") is None


def test_event_owner_can_read_venue_through_member_access(repository):
    repository.save_for_owner(
        event_id="event-1", owner_id="owner", venue_name="City Park",
        address="Main Street 1", latitude=None, longitude=None,
    )
    assert repository.is_member(event_id="event-1", user_id="owner")
    assert repository.get_for_member(event_id="event-1", user_id="owner") is not None


def test_non_owner_cannot_read_write_or_delete_venue(repository):
    assert repository.get_for_owner(event_id="event-1", owner_id="intruder") is None
    assert repository.save_for_owner(
        event_id="event-1", owner_id="intruder", venue_name="Hidden",
        address="Private Road", latitude=None, longitude=None,
    ) is None
    assert not repository.delete_for_owner(event_id="event-1", owner_id="intruder")


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(91, 0), (0, 181), (50, None), (None, 6)],
)
def test_invalid_coordinates_are_rejected(repository, latitude, longitude):
    with pytest.raises(ValueError):
        repository.save_for_owner(
            event_id="event-1", owner_id="owner", venue_name="Park",
            address="Main Street", latitude=latitude, longitude=longitude,
        )


def test_event_deletion_cascades_to_venue(repository):
    repository.save_for_owner(
        event_id="event-1", owner_id="owner", venue_name="Park",
        address="Main Street", latitude=None, longitude=None,
    )
    repository._connection.execute("DELETE FROM events WHERE id = ?", ("event-1",))
    assert repository._connection.execute(
        "SELECT 1 FROM event_venues WHERE event_id = ?", ("event-1",)
    ).fetchone() is None

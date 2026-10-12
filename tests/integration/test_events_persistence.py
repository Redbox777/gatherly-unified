import sqlite3

import pytest

from app.application.events import EventNotFoundError, EventsService
from app.infrastructure.database.connection import migrate
from app.infrastructure.database.events_repository import EventsRepository


@pytest.fixture
def service():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    yield EventsService(EventsRepository(connection))
    connection.close()


def test_create_and_retrieve_event_for_owner(service: EventsService) -> None:
    created = service.create_event(owner_id="alice", title="Weekend trip")
    fetched = service.get_event(event_id=created.id, owner_id="alice")
    assert fetched.id == created.id
    assert fetched.title == "Weekend trip"


def test_other_owner_cannot_retrieve_event(service: EventsService) -> None:
    created = service.create_event(owner_id="alice", title="Private event")
    with pytest.raises(EventNotFoundError, match="Event not found"):
        service.get_event(event_id=created.id, owner_id="bob")


def test_migration_is_idempotent() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        migrate(connection)
        migrate(connection)
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        assert versions == [(1,)]
    finally:
        connection.close()

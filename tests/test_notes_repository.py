from datetime import UTC, datetime
import sqlite3

import pytest

from app.infrastructure.database.connection import migrate
from app.infrastructure.database.notes_repository import NotesRepository


@pytest.fixture
def notes_db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC).isoformat()
    connection.executemany(
        "INSERT INTO users(id, created_at) VALUES (?, ?)",
        [("owner", now), ("member", now), ("outsider", now)],
    )
    connection.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) VALUES (?, ?, ?, ?, ?)",
        ("event-1", "owner", "Planning", None, now),
    )
    connection.execute(
        """INSERT INTO event_participants(
            id, event_id, display_name, added_at, linked_user_id
        ) VALUES (?, ?, ?, ?, ?)""",
        ("participant-1", "event-1", "Member", now, "member"),
    )
    connection.commit()
    yield connection
    connection.close()


def test_private_notes_are_visible_only_to_author(notes_db) -> None:
    repository = NotesRepository(notes_db)
    private = repository.create(
        event_id="event-1", author_id="member", content="Personal thought",
        visibility="private", now=datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
    )
    assert private is not None
    assert len(repository.list_for_event(event_id="event-1", user_id="member") or []) == 1
    assert repository.list_for_event(event_id="event-1", user_id="owner") == []


def test_shared_notes_are_visible_to_event_owner_and_linked_members(notes_db) -> None:
    repository = NotesRepository(notes_db)
    repository.create(
        event_id="event-1", author_id="member", content="Bring supplies",
        visibility="shared", now=datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
    )
    assert len(repository.list_for_event(event_id="event-1", user_id="owner") or []) == 1
    assert len(repository.list_for_event(event_id="event-1", user_id="member") or []) == 1


def test_outsider_cannot_read_or_create_event_notes(notes_db) -> None:
    repository = NotesRepository(notes_db)
    assert repository.list_for_event(event_id="event-1", user_id="outsider") is None
    assert repository.create(
        event_id="event-1", author_id="outsider", content="No access",
        visibility="shared", now=datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
    ) is None


def test_only_author_can_edit_and_owner_can_moderate_shared_notes(notes_db) -> None:
    repository = NotesRepository(notes_db)
    note = repository.create(
        event_id="event-1", author_id="member", content="Initial",
        visibility="shared", now=datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
    )
    assert note is not None
    assert repository.update(
        event_id="event-1", note_id=str(note["id"]), user_id="owner",
        content="Tampered", visibility="shared",
        now=datetime(2026, 11, 13, 12, 1, tzinfo=UTC),
    ) is None
    assert repository.delete(
        event_id="event-1", note_id=str(note["id"]), user_id="owner"
    )


def test_note_validation_and_edit_persistence(notes_db) -> None:
    repository = NotesRepository(notes_db)
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        repository.create(
            event_id="event-1", author_id="owner", content="  ",
            visibility="private", now=now,
        )
    note = repository.create(
        event_id="event-1", author_id="owner", content="Draft",
        visibility="private", now=now,
    )
    assert note is not None
    updated = repository.update(
        event_id="event-1", note_id=str(note["id"]), user_id="owner",
        content="Final", visibility="private",
        now=datetime(2026, 11, 13, 12, 1, tzinfo=UTC),
    )
    assert updated is not None
    assert updated["content"] == "Final"


def test_note_membership_checks_are_inside_the_database_transaction(notes_db) -> None:
    repository = NotesRepository(notes_db)
    original_check = repository._is_member
    checked_in_transaction: list[bool] = []

    def tracked_check(*, event_id: str, user_id: str) -> bool:
        checked_in_transaction.append(notes_db.in_transaction)
        return original_check(event_id=event_id, user_id=user_id)

    repository._is_member = tracked_check  # type: ignore[method-assign]

    assert repository.list_for_event(event_id="event-1", user_id="member") == []
    created = repository.create(
        event_id="event-1",
        author_id="member",
        content="Created under membership lock",
        visibility="private",
        now=datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
    )
    assert created is not None
    assert checked_in_transaction == [True, True]

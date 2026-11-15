"""Persistence and authorization tests for event planning tasks."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.event_tasks_repository import EventTasksRepository


@pytest.fixture
def repository(tmp_path):
    connection = connect(tmp_path / "tasks.sqlite3")
    migrate(connection)
    now = datetime.now(UTC).isoformat()
    connection.execute(
        "INSERT INTO users(id, created_at) VALUES (?, ?)", ("owner", now)
    )
    connection.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) VALUES (?, ?, ?, ?, ?)",
        ("event-1", "owner", "Planning", None, now),
    )
    connection.execute(
        "INSERT INTO event_participants(id, event_id, display_name, added_at) VALUES (?, ?, ?, ?)",
        ("participant-1", "event-1", "Alex", now),
    )
    connection.commit()
    yield EventTasksRepository(connection)
    connection.close()


def test_owner_can_create_assign_complete_update_and_delete_task(repository):
    now = datetime.now(UTC)
    created = repository.create(
        event_id="event-1", owner_id="owner", title="Book a room",
        description="Call the venue", due_at=now.isoformat(),
        assignee_participant_id="participant-1", now=now,
    )
    assert created is not None
    assert created["assignee_participant_id"] == "participant-1"
    assert created["display_name"] == "Alex"
    completed = repository.set_completed(
        task_id=str(created["id"]), event_id="event-1",
        owner_id="owner", completed=True,
    )
    assert completed is not None
    assert completed["completed"] is True
    listed = repository.list_for_owner(event_id="event-1", owner_id="owner")
    assert listed is not None
    assert listed[0]["completed"] is True
    updated = repository.update(
        task_id=str(created["id"]), event_id="event-1", owner_id="owner",
        title="Reserve a room", description="Confirm capacity",
        due_at=None, assignee_participant_id=None,
    )
    assert updated is not None
    assert updated["title"] == "Reserve a room"
    assert updated["completed"] is True
    assert repository.delete(
        task_id=str(created["id"]), event_id="event-1", owner_id="owner"
    )


def test_non_owner_cannot_list_or_mutate_event_tasks(repository):
    created = repository.create(
        event_id="event-1", owner_id="owner", title="Prepare agenda",
        description="", due_at=None, assignee_participant_id=None,
        now=datetime.now(UTC),
    )
    assert created is not None
    assert repository.list_for_owner(event_id="event-1", owner_id="intruder") is None
    assert repository.set_completed(
        task_id=str(created["id"]), event_id="event-1",
        owner_id="intruder", completed=True,
    ) is None
    assert not repository.delete(
        task_id=str(created["id"]), event_id="event-1", owner_id="intruder"
    )


def test_assignee_must_be_a_participant_of_the_same_event(repository):
    with pytest.raises(ValueError, match="belong to this event"):
        repository.create(
            event_id="event-1", owner_id="owner", title="Prepare agenda",
            description="", due_at=None,
            assignee_participant_id=str(uuid4()), now=datetime.now(UTC),
        )


def test_invalid_task_title_is_rejected(repository):
    with pytest.raises(ValueError, match="Task title"):
        repository.create(
            event_id="event-1", owner_id="owner", title="  ",
            description="", due_at=None, assignee_participant_id=None,
            now=datetime.now(UTC),
        )



def test_task_text_update_does_not_reset_completion(repository):
    now = datetime.now(UTC)
    created = repository.create(
        event_id="event-1",
        owner_id="owner",
        title="Prepare agenda",
        description="Initial",
        due_at=None,
        assignee_participant_id=None,
        now=now,
    )
    assert created is not None
    completed = repository.set_completed(
        task_id=str(created["id"]),
        event_id="event-1",
        owner_id="owner",
        completed=True,
    )
    assert completed is not None and completed["completed"] is True

    updated = repository.update(
        task_id=str(created["id"]),
        event_id="event-1",
        owner_id="owner",
        title="Finalize agenda",
        description="Updated details",
        due_at=None,
        assignee_participant_id=None,
    )
    assert updated is not None
    assert updated["completed"] is True


def test_task_assignment_validation_runs_inside_write_transaction(repository):
    original = repository._valid_assignee
    transaction_states = []

    def tracked(event_id, participant_id):
        transaction_states.append(repository._connection.in_transaction)
        return original(event_id, participant_id)

    repository._valid_assignee = tracked

    created = repository.create(
        event_id="event-1",
        owner_id="owner",
        title="Prepare agenda",
        description="",
        due_at=None,
        assignee_participant_id="participant-1",
        now=datetime.now(UTC),
    )
    assert created is not None
    assert transaction_states == [True]


def test_non_owner_cannot_update_or_complete_another_owners_task(repository):
    created = repository.create(
        event_id="event-1",
        owner_id="owner",
        title="Private task",
        description="",
        due_at=None,
        assignee_participant_id=None,
        now=datetime.now(UTC),
    )
    assert created is not None
    task_id = str(created["id"])

    assert repository.update(
        task_id=task_id,
        event_id="event-1",
        owner_id="intruder",
        title="Tampered",
        description="",
        due_at=None,
        assignee_participant_id=None,
    ) is None
    assert repository.set_completed(
        task_id=task_id,
        event_id="event-1",
        owner_id="intruder",
        completed=True,
    ) is None
    unchanged = repository.get_for_owner(
        task_id=task_id, event_id="event-1", owner_id="owner"
    )
    assert unchanged is not None
    assert unchanged["title"] == "Private task"
    assert unchanged["completed"] is False

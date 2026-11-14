"""Persistence, validation, and ownership tests for event expenses."""

from datetime import UTC, datetime

import pytest

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.event_expenses_repository import EventExpensesRepository


@pytest.fixture
def repository(tmp_path):
    connection = connect(tmp_path / "expenses.sqlite3")
    migrate(connection)
    now = datetime.now(UTC).isoformat()
    connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("owner", now))
    connection.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) VALUES (?, ?, ?, ?, ?)",
        ("event-1", "owner", "Dinner", None, now),
    )
    connection.execute(
        "INSERT INTO event_participants(id, event_id, display_name, added_at) VALUES (?, ?, ?, ?)",
        ("participant-1", "event-1", "Alex", now),
    )
    connection.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) VALUES (?, ?, ?, ?, ?)",
        ("event-2", "owner", "Other event", None, now),
    )
    connection.commit()
    yield EventExpensesRepository(connection)
    connection.close()


def _create(repository, **overrides):
    values = {
        "event_id": "event-1",
        "owner_id": "owner",
        "title": "Dinner",
        "description": "Food and drinks",
        "amount_minor_units": 1234,
        "currency": "EUR",
        "paid_by_participant_id": "participant-1",
        "incurred_at": datetime(2026, 11, 14, 18, tzinfo=UTC),
        "now": datetime(2026, 11, 14, 19, tzinfo=UTC),
    }
    values.update(overrides)
    return repository.create(**values)


def test_owner_can_create_list_update_and_delete_expense(repository):
    created = _create(repository)
    assert created is not None
    assert created["title"] == "Dinner"
    assert created["amount_minor_units"] == 1234
    assert created["currency"] == "EUR"
    assert created["paid_by_name"] == "Alex"
    assert len(repository.list_for_owner(event_id="event-1", owner_id="owner")) == 1

    updated = repository.update(
        expense_id=created["id"],
        event_id="event-1",
        owner_id="owner",
        title="Restaurant",
        description="Updated",
        amount_minor_units=2500,
        currency="EUR",
        paid_by_participant_id="participant-1",
        incurred_at=datetime(2026, 11, 14, 20, tzinfo=UTC),
        now=datetime(2026, 11, 14, 21, tzinfo=UTC),
    )
    assert updated is not None
    assert updated["title"] == "Restaurant"
    assert updated["amount_minor_units"] == 2500
    assert repository.delete(
        expense_id=created["id"], event_id="event-1", owner_id="owner"
    )
    assert repository.list_for_owner(event_id="event-1", owner_id="owner") == []


def test_non_owner_cannot_list_read_update_or_delete_expenses(repository):
    created = _create(repository)
    assert created is not None
    assert repository.list_for_owner(event_id="event-1", owner_id="intruder") is None
    assert repository.get_for_owner(
        expense_id=created["id"], event_id="event-1", owner_id="intruder"
    ) is None
    assert repository.update(
        expense_id=created["id"],
        event_id="event-1",
        owner_id="intruder",
        title="Changed",
        description="",
        amount_minor_units=1,
        currency="EUR",
        paid_by_participant_id="participant-1",
        incurred_at=datetime.now(UTC),
        now=datetime.now(UTC),
    ) is None
    assert not repository.delete(
        expense_id=created["id"], event_id="event-1", owner_id="intruder"
    )


def test_expense_requires_payer_from_same_event(repository):
    with pytest.raises(ValueError, match="Payer"):
        _create(repository, paid_by_participant_id="not-a-participant")


@pytest.mark.parametrize("amount", [0, -1, 1.5, True])
def test_expense_rejects_non_positive_or_non_integer_minor_units(repository, amount):
    with pytest.raises((ValueError, TypeError)):
        _create(repository, amount_minor_units=amount)


def test_expense_rejects_unknown_currency_and_invalid_text(repository):
    with pytest.raises(ValueError, match="Unsupported currency"):
        _create(repository, currency="XYZ")
    with pytest.raises(ValueError, match="title"):
        _create(repository, title="  ")


def test_listing_nonexistent_event_returns_none(repository):
    assert repository.list_for_owner(event_id="missing", owner_id="owner") is None


def test_deleting_event_cascades_to_expenses(repository):
    created = _create(repository)
    assert created is not None
    connection = repository._connection
    connection.execute("DELETE FROM events WHERE id = ?", ("event-1",))
    assert connection.execute(
        "SELECT 1 FROM event_expenses WHERE event_id = ?", ("event-1",)
    ).fetchone() is None

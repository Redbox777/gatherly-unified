"""Persistence and ownership tests for expense splits."""

from datetime import UTC, datetime

import pytest

from app.domain.expense_splits import SplitInput, SplitMode, calculate_split
from app.domain.money import Money
from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.event_expense_splits_repository import (
    EventExpenseSplitsRepository,
)
from app.infrastructure.database.event_expenses_repository import EventExpensesRepository


@pytest.fixture
def repositories(tmp_path):
    connection = connect(tmp_path / "expense-splits.sqlite3")
    migrate(connection)
    now = datetime.now(UTC).isoformat()
    connection.execute("INSERT INTO users(id, created_at) VALUES (?, ?)", ("owner", now))
    connection.execute(
        "INSERT INTO events(id, owner_id, title, starts_at, created_at) VALUES (?, ?, ?, ?, ?)",
        ("event-1", "owner", "Dinner", None, now),
    )
    for participant_id, name in [("p1", "Alex"), ("p2", "Sam"), ("p3", "Guest")]:
        connection.execute(
            """INSERT INTO event_participants(id, event_id, display_name, added_at)
               VALUES (?, ?, ?, ?)""",
            (participant_id, "event-1", name, now),
        )
    connection.commit()
    expenses = EventExpensesRepository(connection)
    expense = expenses.create(
        event_id="event-1", owner_id="owner", title="Dinner", description="",
        amount_minor_units=1000, currency="EUR", paid_by_participant_id="p1",
        incurred_at=datetime.now(UTC), now=datetime.now(UTC),
    )
    assert expense is not None
    yield connection, EventExpenseSplitsRepository(connection), expenses, expense["id"]
    connection.close()


def test_owner_can_save_and_read_split(repositories) -> None:
    _, splits, _, expense_id = repositories
    shares = calculate_split(
        Money(1000, "EUR"), SplitMode.EQUAL,
        [SplitInput("p1"), SplitInput("p2"), SplitInput("p3")],
    )
    saved = splits.replace_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="owner",
        mode=SplitMode.EQUAL, shares=shares,
    )
    assert saved is not None
    assert saved["mode"] == "equal"
    assert sum(row["amount_minor_units"] for row in saved["allocations"]) == 1000
    assert splits.get_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="owner"
    ) == saved


def test_split_rejects_participant_from_another_event(repositories) -> None:
    _, splits, _, expense_id = repositories
    shares = calculate_split(
        Money(1000, "EUR"), SplitMode.EQUAL, [SplitInput("not-in-event")]
    )
    with pytest.raises(ValueError, match="belong"):
        splits.replace_for_owner(
            event_id="event-1", expense_id=expense_id, owner_id="owner",
            mode=SplitMode.EQUAL, shares=shares,
        )


def test_non_owner_cannot_read_or_write_split(repositories) -> None:
    _, splits, _, expense_id = repositories
    assert splits.get_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="intruder"
    ) is None
    shares = calculate_split(
        Money(1000, "EUR"), SplitMode.EQUAL, [SplitInput("p1")]
    )
    assert splits.replace_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="intruder",
        mode=SplitMode.EQUAL, shares=shares,
    ) is None


def test_changing_expense_total_clears_old_split(repositories) -> None:
    _, splits, expenses, expense_id = repositories
    shares = calculate_split(
        Money(1000, "EUR"), SplitMode.EQUAL, [SplitInput("p1"), SplitInput("p2")]
    )
    splits.replace_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="owner",
        mode=SplitMode.EQUAL, shares=shares,
    )
    updated = expenses.update(
        expense_id=expense_id, event_id="event-1", owner_id="owner",
        title="Dinner", description="", amount_minor_units=1200, currency="EUR",
        paid_by_participant_id="p1", incurred_at=datetime.now(UTC), now=datetime.now(UTC),
    )
    assert updated is not None
    assert splits.get_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="owner"
    )["mode"] is None



def test_description_update_preserves_existing_split(repositories) -> None:
    _, splits, expenses, expense_id = repositories
    shares = calculate_split(
        Money(1000, "EUR"), SplitMode.EQUAL, [SplitInput("p1"), SplitInput("p2")]
    )
    splits.replace_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="owner",
        mode=SplitMode.EQUAL, shares=shares,
    )
    updated = expenses.update(
        expense_id=expense_id, event_id="event-1", owner_id="owner",
        title="Dinner", description="Updated notes", amount_minor_units=1000,
        currency="EUR", paid_by_participant_id="p1",
        incurred_at=datetime.now(UTC), now=datetime.now(UTC),
    )
    assert updated is not None
    assert splits.get_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="owner"
    )["mode"] == "equal"


def test_removing_participant_preserves_allocation_snapshot(repositories) -> None:
    connection, splits, _, expense_id = repositories
    shares = calculate_split(
        Money(1000, "EUR"), SplitMode.EQUAL, [SplitInput("p1"), SplitInput("p2")]
    )
    splits.replace_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="owner",
        mode=SplitMode.EQUAL, shares=shares,
    )
    connection.execute("DELETE FROM event_participants WHERE id = ?", ("p2",))
    result = splits.get_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="owner"
    )
    assert result is not None
    removed = next(row for row in result["allocations"] if row["participant_name"] == "Sam")
    assert removed["participant_id"] is None
    assert removed["amount_minor_units"] == 500

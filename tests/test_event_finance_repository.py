"""Persistence, ownership, and settlement validation tests."""

from datetime import UTC, datetime

import pytest

from app.domain.expense_splits import SplitInput, SplitMode, calculate_split
from app.domain.money import Money
from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.event_expense_splits_repository import (
    EventExpenseSplitsRepository,
)
from app.infrastructure.database.event_expenses_repository import EventExpensesRepository
from app.infrastructure.database.event_finance_repository import EventFinanceRepository


@pytest.fixture
def finance(tmp_path):
    connection = connect(tmp_path / "event-finance.sqlite3")
    migrate(connection)
    now = datetime.now(UTC)
    connection.execute(
        "INSERT INTO users(id, created_at) VALUES (?, ?)",
        ("owner", now.isoformat()),
    )
    connection.execute(
        """INSERT INTO events(id, owner_id, title, starts_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("event-1", "owner", "Dinner", None, now.isoformat()),
    )
    for participant_id, name in [("a", "Alex"), ("b", "Boris"), ("c", "Casey")]:
        connection.execute(
            """INSERT INTO event_participants(id, event_id, display_name, added_at)
               VALUES (?, ?, ?, ?)""",
            (participant_id, "event-1", name, now.isoformat()),
        )
    connection.commit()
    expenses = EventExpensesRepository(connection)
    splits = EventExpenseSplitsRepository(connection)
    repository = EventFinanceRepository(connection)
    yield connection, expenses, splits, repository
    connection.close()


def add_expense(finance, *, amount=3000, currency="EUR", payer="a"):
    _, expenses, _, _ = finance
    expense = expenses.create(
        event_id="event-1", owner_id="owner", title="Dinner", description="",
        amount_minor_units=amount, currency=currency,
        paid_by_participant_id=payer, incurred_at=datetime.now(UTC),
        now=datetime.now(UTC),
    )
    assert expense is not None
    return expense


def save_equal_split(finance, expense_id, ids=("a", "b", "c")):
    _, _, splits, _ = finance
    shares = calculate_split(
        Money(3000, "EUR"), SplitMode.EQUAL, [SplitInput(item) for item in ids]
    )
    return splits.replace_for_owner(
        event_id="event-1", expense_id=expense_id, owner_id="owner",
        mode=SplitMode.EQUAL, shares=shares,
    )


def test_balances_are_owner_scoped_and_skip_unallocated_expenses(finance) -> None:
    _, _, _, repository = finance
    add_expense(finance)
    result = repository.get_balances_for_owner(
        event_id="event-1", owner_id="owner"
    )
    assert result is not None
    assert result["balances"] == []
    assert result["pending_expenses"] == [
        {"currency": "EUR", "count": 1, "amount_minor_units": 3000}
    ]
    assert repository.get_balances_for_owner(
        event_id="event-1", owner_id="intruder"
    ) is None


def test_settlement_reduces_debt_and_creditor_balance(finance) -> None:
    _, _, _, repository = finance
    expense = add_expense(finance)
    assert save_equal_split(finance, expense["id"]) is not None

    created = repository.create_settlement_for_owner(
        event_id="event-1", owner_id="owner",
        from_participant_id="b", to_participant_id="a",
        amount_minor_units=500, currency="EUR",
        settled_at=datetime.now(UTC), note="Cash", now=datetime.now(UTC),
    )
    assert created is not None
    assert created["from_participant_name"] == "Boris"
    assert created["to_participant_name"] == "Alex"

    result = repository.get_balances_for_owner(
        event_id="event-1", owner_id="owner"
    )
    assert result is not None
    balances = {
        item["participant_id"]: item["balance_minor_units"]
        for item in result["balances"]
        if item["currency"] == "EUR"
    }
    assert balances == {"a": 1500, "b": -500, "c": -1000}
    listed = repository.list_settlements_for_owner(
        event_id="event-1", owner_id="owner"
    )
    assert listed is not None
    assert len(listed) == 1


def test_settlement_cannot_exceed_outstanding_balance(finance) -> None:
    _, _, _, repository = finance
    expense = add_expense(finance)
    save_equal_split(finance, expense["id"])
    with pytest.raises(ValueError, match="exceeds"):
        repository.create_settlement_for_owner(
            event_id="event-1", owner_id="owner",
            from_participant_id="b", to_participant_id="a",
            amount_minor_units=1001, currency="EUR",
            settled_at=datetime.now(UTC), note="", now=datetime.now(UTC),
        )
    assert repository.list_settlements_for_owner(
        event_id="event-1", owner_id="owner"
    ) == []


def test_settlement_requires_debtor_and_creditor_in_same_event(finance) -> None:
    connection, _, _, repository = finance
    expense = add_expense(finance)
    save_equal_split(finance, expense["id"])
    with pytest.raises(ValueError, match="owes"):
        repository.create_settlement_for_owner(
            event_id="event-1", owner_id="owner",
            from_participant_id="a", to_participant_id="b",
            amount_minor_units=100, currency="EUR",
            settled_at=datetime.now(UTC), note="", now=datetime.now(UTC),
        )
    connection.execute(
        """INSERT INTO events(id, owner_id, title, starts_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("event-2", "owner", "Other", None, datetime.now(UTC).isoformat()),
    )
    connection.execute(
        """INSERT INTO event_participants(id, event_id, display_name, added_at)
           VALUES (?, ?, ?, ?)""",
        ("outside", "event-2", "Outside", datetime.now(UTC).isoformat()),
    )
    connection.commit()
    with pytest.raises(ValueError, match="belong"):
        repository.create_settlement_for_owner(
            event_id="event-1", owner_id="owner",
            from_participant_id="b", to_participant_id="outside",
            amount_minor_units=100, currency="EUR",
            settled_at=datetime.now(UTC), note="", now=datetime.now(UTC),
        )


def test_payer_and_settlement_names_survive_participant_deletion(finance) -> None:
    connection, expenses, splits, repository = finance
    expense = add_expense(finance, payer="b")
    shares = calculate_split(
        Money(3000, "EUR"), SplitMode.EQUAL,
        [SplitInput("a"), SplitInput("b"), SplitInput("c")],
    )
    splits.replace_for_owner(
        event_id="event-1", expense_id=expense["id"], owner_id="owner",
        mode=SplitMode.EQUAL, shares=shares,
    )
    repository.create_settlement_for_owner(
        event_id="event-1", owner_id="owner",
        from_participant_id="a", to_participant_id="b",
        amount_minor_units=500, currency="EUR",
        settled_at=datetime.now(UTC), note="", now=datetime.now(UTC),
    )
    connection.execute("DELETE FROM event_participants WHERE id = ?", ("b",))
    connection.commit()

    stored_expense = expenses.get_for_owner(
        expense_id=expense["id"], event_id="event-1", owner_id="owner"
    )
    assert stored_expense is not None
    assert stored_expense["paid_by_participant_id"] is None
    assert stored_expense["paid_by_name"] == "Boris"
    stored_settlements = repository.list_settlements_for_owner(
        event_id="event-1", owner_id="owner"
    )
    assert stored_settlements is not None
    assert stored_settlements[0]["to_participant_id"] is None
    assert stored_settlements[0]["to_participant_name"] == "Boris"

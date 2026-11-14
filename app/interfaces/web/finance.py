"""Event expense, split, balance, and settlement HTTP routes."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.domain.expense_splits import SplitInput, SplitMode, calculate_split
from app.domain.money import Money, get_currency
from app.interfaces.web.constants import SESSION_COOKIE
from app.interfaces.web.dependencies import Dependencies
from app.interfaces.web.schemas import (
    EventExpenseRequest,
    EventSettlementRequest,
    ExpenseSplitRequest,
)
from app.interfaces.web.serializers import (
    _format_finance_amount,
    _serialize_event_expense,
    _serialize_minor_amount,
    _serialize_settlement,
)


def create_finance_router(
    services: Callable[..., Iterator[Dependencies]],
) -> APIRouter:
    """Build finance endpoints with the app's request-scoped service dependency."""
    router = APIRouter()
    ServiceDependency = Annotated[Dependencies, Depends(services)]

    @router.get("/events/{event_id}/expenses")
    def list_event_expenses(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> list[dict[str, object]]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        expenses = deps.event_expenses.list_for_owner(
            event_id=str(event_id), owner_id=owner_id
        )
        if expenses is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return [_serialize_event_expense(expense) for expense in expenses]

    @router.post(
        "/events/{event_id}/expenses", status_code=status.HTTP_201_CREATED
    )
    def create_event_expense(
        event_id: UUID, payload: EventExpenseRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        now = datetime.now(UTC)
        incurred_at = payload.incurred_at or now
        if incurred_at.tzinfo is None or incurred_at.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Expense date must include a timezone")
        try:
            money = Money.from_major(payload.amount, payload.currency)
            expense = deps.event_expenses.create(
                event_id=str(event_id), owner_id=owner_id,
                title=payload.title, description=payload.description,
                amount_minor_units=money.minor_units, currency=money.currency,
                paid_by_participant_id=str(payload.paid_by_participant_id),
                incurred_at=incurred_at, now=now,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid expense data") from error
        if expense is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return _serialize_event_expense(expense)

    @router.put("/events/{event_id}/expenses/{expense_id}")
    def update_event_expense(
        event_id: UUID, expense_id: UUID, payload: EventExpenseRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        now = datetime.now(UTC)
        incurred_at = payload.incurred_at or now
        if incurred_at.tzinfo is None or incurred_at.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Expense date must include a timezone")
        try:
            money = Money.from_major(payload.amount, payload.currency)
            expense = deps.event_expenses.update(
                expense_id=str(expense_id), event_id=str(event_id), owner_id=owner_id,
                title=payload.title, description=payload.description,
                amount_minor_units=money.minor_units, currency=money.currency,
                paid_by_participant_id=str(payload.paid_by_participant_id),
                incurred_at=incurred_at, now=now,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid expense data") from error
        if expense is None:
            raise HTTPException(status_code=404, detail="Expense not found")
        return _serialize_event_expense(expense)

    @router.get("/events/{event_id}/expenses/{expense_id}/split")
    def get_event_expense_split(
        event_id: UUID, expense_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        split = deps.event_expense_splits.get_for_owner(
            event_id=str(event_id), expense_id=str(expense_id), owner_id=owner_id
        )
        if split is None:
            raise HTTPException(status_code=404, detail="Expense not found")
        currency = split["currency"]
        if not isinstance(currency, str):
            raise TypeError("Expense currency must be a string")
        precision = get_currency(currency).minor_unit_digits
        allocations = []
        for allocation in cast(list[dict[str, object]], split["allocations"]):
            minor_units = allocation["amount_minor_units"]
            if isinstance(minor_units, bool) or not isinstance(minor_units, int):
                raise TypeError("Allocation amount must be integer minor units")
            money = Money(minor_units, currency)
            allocations.append({
                **allocation,
                "amount": f"{money.major_units:.{precision}f}",
            })
        return {**split, "allocations": allocations}

    @router.put("/events/{event_id}/expenses/{expense_id}/split")
    def replace_event_expense_split(
        event_id: UUID, expense_id: UUID, payload: ExpenseSplitRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            mode = SplitMode(payload.mode)
            split = deps.event_expense_splits.get_for_owner(
                event_id=str(event_id), expense_id=str(expense_id), owner_id=owner_id
            )
            if split is None:
                raise HTTPException(status_code=404, detail="Expense not found")
            currency = split["currency"]
            if not isinstance(currency, str):
                raise TypeError("Expense currency must be a string")
            inputs = []
            for allocation in payload.allocations:
                amount_minor_units = None
                if allocation.amount is not None:
                    amount_minor_units = Money.from_major(
                        allocation.amount, currency
                    ).minor_units
                inputs.append(SplitInput(
                    participant_id=str(allocation.participant_id),
                    amount_minor_units=amount_minor_units,
                    weight=allocation.weight,
                ))
            total_minor_units = split["amount_minor_units"]
            if isinstance(total_minor_units, bool) or not isinstance(total_minor_units, int):
                raise TypeError("Expense amount must be integer minor units")
            shares = calculate_split(
                Money(total_minor_units, currency), mode, inputs
            )
            saved = deps.event_expense_splits.replace_for_owner(
                event_id=str(event_id), expense_id=str(expense_id),
                owner_id=owner_id, mode=mode, shares=shares,
            )
        except HTTPException:
            raise
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid expense split") from error
        if saved is None:
            raise HTTPException(status_code=404, detail="Expense not found")
        precision = get_currency(currency).minor_unit_digits
        result_allocations = []
        for saved_allocation in cast(
            list[dict[str, object]], saved["allocations"]
        ):
            saved_minor_units = saved_allocation["amount_minor_units"]
            if isinstance(saved_minor_units, bool) or not isinstance(saved_minor_units, int):
                raise TypeError("Allocation amount must be integer minor units")
            money = Money(saved_minor_units, currency)
            result_allocations.append({
                **saved_allocation,
                "amount": f"{money.major_units:.{precision}f}",
            })
        return {**saved, "allocations": result_allocations}

    @router.get("/events/{event_id}/balances")
    def get_event_balances(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        result = deps.event_finance.get_balances_for_owner(
            event_id=str(event_id), owner_id=owner_id
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Event not found")
        balances = cast(list[dict[str, object]], result["balances"])
        pending = cast(list[dict[str, object]], result["pending_expenses"])
        unresolved = cast(list[dict[str, object]], result["unresolved"])
        return {
            **result,
            "balances": [
                _serialize_minor_amount(item, "balance_minor_units")
                for item in balances
            ],
            "pending_expenses": [
                _serialize_minor_amount(item, "amount_minor_units")
                for item in pending
            ],
            "unresolved": [
                {
                    **item,
                    "missing_payer": _format_finance_amount(
                        item["missing_payer_minor_units"], item["currency"]
                    ),
                    "removed_participant_allocations": _format_finance_amount(
                        item["removed_participant_allocations_minor_units"],
                        item["currency"],
                    ),
                    "settlements_with_removed_participant": _format_finance_amount(
                        item["settlements_with_removed_participant_minor_units"],
                        item["currency"],
                    ),
                }
                for item in unresolved
            ],
        }

    @router.get("/events/{event_id}/settlements")
    def list_event_settlements(
        event_id: UUID, request: Request, deps: ServiceDependency
    ) -> list[dict[str, object]]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        settlements = deps.event_finance.list_settlements_for_owner(
            event_id=str(event_id), owner_id=owner_id
        )
        if settlements is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return [_serialize_settlement(item) for item in settlements]

    @router.post("/events/{event_id}/settlements", status_code=status.HTTP_201_CREATED)
    def create_event_settlement(
        event_id: UUID, payload: EventSettlementRequest,
        request: Request, deps: ServiceDependency
    ) -> dict[str, object]:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        now = datetime.now(UTC)
        settled_at = payload.settled_at or now
        if settled_at.tzinfo is None or settled_at.utcoffset() is None:
            raise HTTPException(status_code=422, detail="Settlement date must include a timezone")
        try:
            money = Money.from_major(payload.amount, payload.currency)
            settlement = deps.event_finance.create_settlement_for_owner(
                event_id=str(event_id), owner_id=owner_id,
                from_participant_id=str(payload.from_participant_id),
                to_participant_id=str(payload.to_participant_id),
                amount_minor_units=money.minor_units, currency=money.currency,
                settled_at=settled_at, note=payload.note, now=now,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid settlement") from error
        if settlement is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return _serialize_settlement(settlement)

    @router.delete(
        "/events/{event_id}/expenses/{expense_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_event_expense(
        event_id: UUID, expense_id: UUID, request: Request, deps: ServiceDependency
    ) -> Response:
        owner_id = deps.authentication.resolve_session(
            raw_token=request.cookies.get(SESSION_COOKIE, ""), now=datetime.now(UTC)
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not deps.event_expenses.delete(
            expense_id=str(expense_id), event_id=str(event_id), owner_id=owner_id
        ):
            raise HTTPException(status_code=404, detail="Expense not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router

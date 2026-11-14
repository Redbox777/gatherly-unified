"""Deterministic participant allocation rules for event expenses."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.money import Money, allocate_equal_shares


class SplitMode(StrEnum):
    """Supported ways to distribute one expense."""

    EQUAL = "equal"
    FIXED = "fixed"
    WEIGHTED = "weighted"


@dataclass(frozen=True, slots=True)
class SplitInput:
    """One participant's input to a configurable split."""

    participant_id: str
    amount_minor_units: int | None = None
    weight: int | None = None


@dataclass(frozen=True, slots=True)
class SplitShare:
    """A validated, exact allocation for one event participant."""

    participant_id: str
    amount: Money
    weight: int | None = None


def calculate_split(
    total: Money, mode: SplitMode, inputs: Sequence[SplitInput]
) -> list[SplitShare]:
    """Calculate exact shares; no float arithmetic or unallocated minor units."""
    if not isinstance(mode, SplitMode):
        raise TypeError("mode must be a SplitMode")
    if not inputs:
        raise ValueError("At least one participant is required")

    participant_ids = [item.participant_id.strip() for item in inputs]
    if any(not participant_id for participant_id in participant_ids):
        raise ValueError("Participant IDs must be non-empty")
    if len(set(participant_ids)) != len(participant_ids):
        raise ValueError("Participant IDs must be unique")

    normalized = sorted(zip(participant_ids, inputs, strict=True), key=lambda pair: pair[0])
    if mode is SplitMode.EQUAL:
        if any(
            item.amount_minor_units is not None or item.weight is not None
            for _, item in normalized
        ):
            raise ValueError("Equal split accepts participant IDs only")
        shares = allocate_equal_shares(total, participant_ids)
        return [SplitShare(pid, shares[pid]) for pid, _ in normalized]

    if mode is SplitMode.FIXED:
        if any(
            item.weight is not None or item.amount_minor_units is None
            for _, item in normalized
        ):
            raise ValueError("Fixed split requires an amount for every participant")
        amounts: dict[str, int] = {}
        for participant_id, item in normalized:
            amount = item.amount_minor_units
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                raise ValueError("Fixed allocations must be positive integer minor units")
            amounts[participant_id] = amount
        if sum(amounts.values()) != total.minor_units:
            raise ValueError("Fixed allocations must equal the expense total")
        return [
            SplitShare(pid, Money(amounts[pid], total.currency))
            for pid, _ in normalized
        ]

    if any(item.amount_minor_units is not None or item.weight is None for _, item in normalized):
        raise ValueError("Weighted split requires a weight for every participant")
    weights: dict[str, int] = {}
    for participant_id, item in normalized:
        weight = item.weight
        if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
            raise ValueError("Weights must be positive integers")
        weights[participant_id] = weight

    total_weight = sum(weights.values())
    sign = -1 if total.minor_units < 0 else 1
    absolute_total = abs(total.minor_units)
    base: dict[str, int] = {}
    remainders: list[tuple[int, str]] = []
    allocated = 0
    for participant_id in sorted(weights):
        quotient, remainder = divmod(absolute_total * weights[participant_id], total_weight)
        base[participant_id] = quotient
        allocated += quotient
        remainders.append((remainder, participant_id))

    leftover = absolute_total - allocated
    for _, participant_id in sorted(remainders, key=lambda item: (-item[0], item[1]))[:leftover]:
        base[participant_id] += 1

    return [
        SplitShare(pid, Money(sign * base[pid], total.currency), weights[pid])
        for pid, _ in normalized
    ]

"""Currency-aware money primitives with exact minor-unit arithmetic."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
)
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Currency:
    """A supported currency and its ISO 4217 minor-unit precision."""

    code: str
    minor_unit_digits: int
    symbol: str


_CURRENCIES: dict[str, Currency] = {
    "CHF": Currency("CHF", 2, "CHF"),
    "EUR": Currency("EUR", 2, "€"),
    "GBP": Currency("GBP", 2, "£"),
    "JPY": Currency("JPY", 0, "¥"),
    "KWD": Currency("KWD", 3, "KWD"),
    "PLN": Currency("PLN", 2, "zł"),
    "RUB": Currency("RUB", 2, "₽"),
    "USD": Currency("USD", 2, "$"),
}


class RoundingMode(StrEnum):
    """Explicit policies for converting decimal input to minor units."""

    HALF_UP = "half_up"
    HALF_EVEN = "half_even"
    DOWN = "down"
    UP = "up"


_ROUNDING_MODES: dict[RoundingMode, str] = {
    RoundingMode.HALF_UP: ROUND_HALF_UP,
    RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
    RoundingMode.DOWN: ROUND_DOWN,
    RoundingMode.UP: ROUND_UP,
}


def get_currency(code: str) -> Currency:
    """Return supported currency metadata; reject unknown or non-canonical codes."""
    if not isinstance(code, str) or code != code.upper():
        raise ValueError("Currency code must be uppercase ISO 4217")
    try:
        return _CURRENCIES[code]
    except KeyError as exc:
        raise ValueError(f"Unsupported currency: {code}") from exc


@dataclass(frozen=True, slots=True)
class Money:
    """An amount stored as integer minor units, never binary floating point."""

    minor_units: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.minor_units, bool) or not isinstance(self.minor_units, int):
            raise TypeError("minor_units must be an integer")
        get_currency(self.currency)

    @classmethod
    def from_major(
        cls,
        amount: str | Decimal,
        currency: str,
        rounding: RoundingMode | None = None,
    ) -> Money:
        """Create money from decimal input.

        Excess precision is rejected unless a rounding policy is explicitly
        supplied. Float input is never accepted.
        """
        if isinstance(amount, bool) or not isinstance(amount, (str, Decimal)):
            raise TypeError("amount must be a decimal string or Decimal")
        try:
            value = Decimal(amount)
        except InvalidOperation as exc:
            raise ValueError("Invalid monetary amount") from exc
        if not value.is_finite():
            raise ValueError("Monetary amount must be finite")

        precision = get_currency(currency).minor_unit_digits
        quantum = Decimal(1).scaleb(-precision)
        if rounding is None:
            scaled = value.scaleb(precision)
            if scaled != scaled.to_integral_value():
                raise ValueError(
                    f"{currency} supports at most {precision} decimal places"
                )
            rounded = value
        else:
            if not isinstance(rounding, RoundingMode):
                raise TypeError("rounding must be a RoundingMode")
            try:
                rounded = value.quantize(quantum, rounding=_ROUNDING_MODES[rounding])
            except InvalidOperation as exc:
                raise ValueError("Monetary amount is outside the supported range") from exc

        return cls(int(rounded.scaleb(precision)), currency)

    @property
    def major_units(self) -> Decimal:
        """Return the exact major-unit amount as Decimal."""
        precision = get_currency(self.currency).minor_unit_digits
        return Decimal(self.minor_units).scaleb(-precision)

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError("Cannot combine amounts in different currencies")

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.minor_units - other.minor_units, self.currency)


def allocate_equal_shares(total: Money, participant_ids: Sequence[str]) -> dict[str, Money]:
    """Split an amount equally, assigning remainder units by sorted participant ID.

    Sorting makes allocations stable if callers provide participants in a
    different order. The sum of returned shares always equals the original total.
    """
    if not participant_ids:
        raise ValueError("At least one participant is required")

    normalized_ids = [participant_id.strip() for participant_id in participant_ids]
    if any(not participant_id for participant_id in normalized_ids):
        raise ValueError("Participant IDs must be non-empty")
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError("Participant IDs must be unique")

    ordered_ids = sorted(normalized_ids)
    sign = -1 if total.minor_units < 0 else 1
    quotient, remainder = divmod(abs(total.minor_units), len(ordered_ids))

    return {
        participant_id: Money(
            sign * (quotient + (1 if index < remainder else 0)),
            total.currency,
        )
        for index, participant_id in enumerate(ordered_ids)
    }

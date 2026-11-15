"""HTTP response serializers for exact money and finance values."""

from app.domain.money import Money, get_currency


def _serialize_event_expense(expense: dict[str, object]) -> dict[str, object]:
    """Expose an exact decimal string alongside the canonical minor-unit amount."""
    currency = expense["currency"]
    amount_minor_units = expense["amount_minor_units"]
    if not isinstance(currency, str):
        raise TypeError("Expense currency must be a string")
    if isinstance(amount_minor_units, bool) or not isinstance(amount_minor_units, int):
        raise TypeError("Expense minor-unit amount must be an integer")
    money = Money(amount_minor_units, currency)
    precision = get_currency(currency).minor_unit_digits
    return {
        **expense,
        "amount": f"{money.major_units:.{precision}f}",
    }


def _format_finance_amount(amount_minor_units: object, currency: object) -> str:
    if not isinstance(currency, str):
        raise TypeError("Finance currency must be a string")
    if (
        isinstance(amount_minor_units, bool)
        or not isinstance(amount_minor_units, int)
    ):
        raise TypeError("Finance amount must be integer minor units")
    precision = get_currency(currency).minor_unit_digits
    money = Money(amount_minor_units, currency)
    return f"{money.major_units:.{precision}f}"


def _serialize_minor_amount(
    item: dict[str, object], field: str
) -> dict[str, object]:
    amount = _format_finance_amount(item.get(field), item.get("currency"))
    return {
        **item,
        field.replace("_minor_units", ""): amount,
        "amount": amount,
    }


def _serialize_settlement(settlement: dict[str, object]) -> dict[str, object]:
    return _serialize_minor_amount(settlement, "amount_minor_units")

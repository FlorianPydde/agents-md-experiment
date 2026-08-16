"""Exact decimal amounts. Money never travels as a float."""

from decimal import Decimal, InvalidOperation

from .errors import InputError

CENTS = Decimal("0.01")


def parse_amount(raw: str, *, field: str) -> Decimal:
    """Turn a decimal string into an exact non negative amount."""
    if not isinstance(raw, str):
        raise InputError(f"{field} must be a decimal amount written as a string")
    try:
        amount = Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        raise InputError(f"{field} is not a decimal amount: {raw!r}") from None
    if not amount.is_finite():
        raise InputError(f"{field} is not a finite amount: {raw!r}")
    if amount < 0:
        raise InputError(f"{field} must not be negative: {raw!r}")
    return amount.quantize(CENTS)


def format_amount(amount: Decimal) -> str:
    """Render an amount with two decimals."""
    return f"{amount.quantize(CENTS):f}"

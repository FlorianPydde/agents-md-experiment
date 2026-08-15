"""Shared domain constants and small value helpers.

Amounts are always represented as `decimal.Decimal`, constructed from the
string values found in the input JSON files, so that no floating point
rounding ever creeps into the balances.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.errors import ValidationError

REQUEST_KINDS = ("goodwill_credit", "account_recovery", "collect_debt")
ACCOUNT_TIERS = ("standard", "premium")
ORIGINS = ("internal", "external")
DECISIONS = ("approve", "reject")

REQUEST_STATES = (
    "received",
    "awaiting_approval",
    "completed",
    "rejected",
    "failed",
)

FINAL_STATES = ("completed", "rejected", "failed")

APPROVAL_ROLES = ("finance", "risk", "supervisor")

# The ordered list of operations that make up the plan for each request kind.
PLANS: dict[str, tuple[str, ...]] = {
    "goodwill_credit": ("read_account", "apply_credit", "notify_customer"),
    "account_recovery": (
        "read_account",
        "unfreeze_account",
        "apply_credit",
        "notify_customer",
    ),
    "collect_debt": ("read_account", "apply_debit", "notify_customer"),
}


def parse_amount(raw: object, *, field: str = "amount") -> Decimal:
    """Parse a decimal amount from a string, rejecting negative values."""
    if not isinstance(raw, str):
        raise ValidationError(f"{field} must be a string decimal amount, got {raw!r}")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field} is not a valid decimal amount: {raw!r}") from exc
    if value < 0:
        raise ValidationError(f"{field} must not be negative: {raw!r}")
    return value


def format_amount(value: Decimal) -> str:
    """Format a decimal amount with exactly two decimal places."""
    return f"{value:.2f}"

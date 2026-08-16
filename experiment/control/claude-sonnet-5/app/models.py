"""Domain constants and small validation helpers shared across the app."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.errors import AppError

REQUEST_KINDS = ("goodwill_credit", "account_recovery", "collect_debt")

ACCOUNT_TIERS = ("standard", "premium")

REQUESTER_ORIGINS = ("internal", "external")

REQUEST_STATES = (
    "received",
    "awaiting_approval",
    "completed",
    "rejected",
    "failed",
)

STEP_STATES = ("pending", "done", "failed", "skipped")

APPROVAL_ROLES = ("finance", "risk", "supervisor")

APPROVAL_STATUSES = ("pending", "approved", "rejected")

# Kind -> ordered list of operation names.
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

OPERATIONS = (
    "read_account",
    "apply_credit",
    "apply_debit",
    "freeze_account",
    "unfreeze_account",
    "notify_customer",
)

# Materiality of each operation.
MATERIALITY: dict[str, str] = {
    "read_account": "read",
    "apply_credit": "write",
    "apply_debit": "write",
    "freeze_account": "write",
    "unfreeze_account": "write",
    "notify_customer": "write",
}

TWO_PLACES = Decimal("0.01")


def parse_amount(raw: object, *, field: str = "amount") -> Decimal:
    """Parse a decimal amount written as a string. Rejects negative amounts."""

    if not isinstance(raw, str):
        raise AppError(f"{field} must be a string holding a decimal amount")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise AppError(f"{field} is not a valid decimal amount: {raw!r}") from exc
    if value < 0:
        raise AppError(f"{field} must not be negative: {raw!r}")
    return value.quantize(TWO_PLACES)


def format_amount(value: Decimal) -> str:
    return str(value.quantize(TWO_PLACES))

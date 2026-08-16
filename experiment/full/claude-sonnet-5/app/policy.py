"""Policy: decides whether a step may run on its own or needs approval.

The first matching rule wins. Returns ``None`` when the step may run on its
own, or the role name required to approve it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.operations import OPERATIONS

GOODWILL_CREDIT_LIMIT = Decimal("100.00")

POLICY_RULES: list[str] = [
    "A read operation runs on its own.",
    "apply_credit of 100.00 or less runs on its own.",
    "apply_credit above 100.00 needs finance.",
    "apply_debit needs finance.",
    "freeze_account and unfreeze_account need risk.",
    "Anything else runs on its own.",
]


def decide(operation_name: str, args: dict[str, Any]) -> str | None:
    """Return the role required to approve the step, or ``None`` if it may
    run on its own."""

    operation = OPERATIONS[operation_name]

    # 1. A read operation runs on its own.
    if operation.materiality == "read":
        return None

    # 2 & 3. apply_credit thresholds.
    if operation_name == "apply_credit":
        amount = args.get("amount")
        if isinstance(amount, Decimal) and amount <= GOODWILL_CREDIT_LIMIT:
            return None
        return "finance"

    # 4. apply_debit needs finance.
    if operation_name == "apply_debit":
        return "finance"

    # 5. freeze_account and unfreeze_account need risk.
    if operation_name in ("freeze_account", "unfreeze_account"):
        return "risk"

    # 6. Anything else runs on its own.
    return None

from __future__ import annotations

from decimal import Decimal
from typing import Any


KINDS = {
    "goodwill_credit": ("read_account", "apply_credit", "notify_customer"),
    "account_recovery": (
        "read_account",
        "unfreeze_account",
        "apply_credit",
        "notify_customer",
    ),
    "collect_debt": ("read_account", "apply_debit", "notify_customer"),
}

OPERATIONS: dict[str, dict[str, str]] = {
    "read_account": {
        "materiality": "read",
        "description": "Reports balance, tier, and frozen state.",
    },
    "apply_credit": {
        "materiality": "write",
        "description": "Increases the account balance.",
    },
    "apply_debit": {
        "materiality": "write",
        "description": "Decreases the account balance.",
    },
    "freeze_account": {
        "materiality": "write",
        "description": "Marks the account frozen.",
    },
    "unfreeze_account": {
        "materiality": "write",
        "description": "Clears the account frozen state.",
    },
    "notify_customer": {
        "materiality": "write",
        "description": "Records a customer notification.",
    },
}

POLICY_RULES = [
    {
        "order": 1,
        "description": "Read operations run autonomously.",
        "required_role": None,
    },
    {
        "order": 2,
        "description": "Credits of 100.00 or less run autonomously.",
        "required_role": None,
    },
    {
        "order": 3,
        "description": "Credits above 100.00 require finance.",
        "required_role": "finance",
    },
    {
        "order": 4,
        "description": "Debits require finance.",
        "required_role": "finance",
    },
    {
        "order": 5,
        "description": "Freeze and unfreeze operations require risk.",
        "required_role": "risk",
    },
    {
        "order": 6,
        "description": "All other operations run autonomously.",
        "required_role": None,
    },
]

NOTIFICATION_TEMPLATES = {
    "internal": "Your service request has been completed.",
    "external": "The requested account service has been completed.",
}


def policy_for(operation: str, arguments: dict[str, Any]) -> tuple[int, str | None]:
    materiality = OPERATIONS[operation]["materiality"]
    if materiality == "read":
        return 1, None
    if operation == "apply_credit" and Decimal(arguments["amount"]) <= Decimal("100.00"):
        return 2, None
    if operation == "apply_credit":
        return 3, "finance"
    if operation == "apply_debit":
        return 4, "finance"
    if operation in {"freeze_account", "unfreeze_account"}:
        return 5, "risk"
    return 6, None

"""Plans: the ordered operations that make up each kind of request."""

from __future__ import annotations

PLANS: dict[str, list[str]] = {
    "goodwill_credit": ["read_account", "apply_credit", "notify_customer"],
    "account_recovery": [
        "read_account",
        "unfreeze_account",
        "apply_credit",
        "notify_customer",
    ],
    "collect_debt": ["read_account", "apply_debit", "notify_customer"],
}


def plan_for(kind: str) -> list[str]:
    return list(PLANS[kind])

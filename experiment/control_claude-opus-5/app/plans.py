"""Plans: the ordered steps each request kind expands into."""

from __future__ import annotations

from typing import Mapping, Sequence

from .models import Kind

PLANS: Mapping[Kind, Sequence[str]] = {
    Kind.GOODWILL_CREDIT: ("read_account", "apply_credit", "notify_customer"),
    Kind.ACCOUNT_RECOVERY: (
        "read_account",
        "unfreeze_account",
        "apply_credit",
        "notify_customer",
    ),
    Kind.COLLECT_DEBT: ("read_account", "apply_debit", "notify_customer"),
}


def plan_for(kind: Kind) -> list[str]:
    return list(PLANS[kind])

"""Static domain knowledge: request kinds, plans, operations and policy.

None of this depends on any particular request or account; it is the fixed
rulebook described in SPEC.md.
"""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

# --- Request kinds and the plans they expand to -----------------------------

REQUEST_KINDS = ("goodwill_credit", "account_recovery", "collect_debt")

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

# --- Requester -----------------------------------------------------------

ORIGINS = ("internal", "external")

# --- Operations --------------------------------------------------------------

OPERATIONS = (
    "read_account",
    "apply_credit",
    "apply_debit",
    "freeze_account",
    "unfreeze_account",
    "notify_customer",
)

MATERIALITY: dict[str, str] = {
    "read_account": "read",
    "apply_credit": "write",
    "apply_debit": "write",
    "freeze_account": "write",
    "unfreeze_account": "write",
    "notify_customer": "write",
}

# --- Approval roles -----------------------------------------------------------

APPROVER_ROLES = ("finance", "risk", "supervisor")

# --- Notification templates --------------------------------------------------

NOTIFY_TEMPLATES = {
    "internal": "Internal notice sent to the requester's team.",
    "external": "Customer notified of the outcome for their request.",
}

GOODWILL_CREDIT_AUTO_LIMIT = Decimal("100.00")


class PolicyDecision(NamedTuple):
    """The outcome of applying policy to a single step."""

    auto: bool
    role: str | None


def decide_policy(operation: str, amount: Decimal | None) -> PolicyDecision:
    """Apply the ordered policy rules from SPEC.md to a step.

    The first matching rule wins:

    1. A read operation runs on its own.
    2. apply_credit of 100.00 or less runs on its own.
    3. apply_credit above 100.00 needs finance.
    4. apply_debit needs finance.
    5. freeze_account and unfreeze_account need risk.
    6. Anything else runs on its own.
    """

    if MATERIALITY[operation] == "read":
        return PolicyDecision(True, None)
    if operation == "apply_credit":
        assert amount is not None
        if amount <= GOODWILL_CREDIT_AUTO_LIMIT:
            return PolicyDecision(True, None)
        return PolicyDecision(False, "finance")
    if operation == "apply_debit":
        return PolicyDecision(False, "finance")
    if operation in ("freeze_account", "unfreeze_account"):
        return PolicyDecision(False, "risk")
    return PolicyDecision(True, None)


def list_policy_rules() -> list[dict]:
    """A human readable, ordered rendering of the policy rules."""

    return [
        {"rule": "A read operation runs on its own.", "operation": "read", "role": None},
        {
            "rule": "apply_credit of 100.00 or less runs on its own.",
            "operation": "apply_credit",
            "condition": "amount <= 100.00",
            "role": None,
        },
        {
            "rule": "apply_credit above 100.00 needs finance.",
            "operation": "apply_credit",
            "condition": "amount > 100.00",
            "role": "finance",
        },
        {"rule": "apply_debit needs finance.", "operation": "apply_debit", "role": "finance"},
        {
            "rule": "freeze_account and unfreeze_account need risk.",
            "operation": "freeze_account, unfreeze_account",
            "role": "risk",
        },
        {"rule": "Anything else runs on its own.", "operation": "*", "role": None},
    ]

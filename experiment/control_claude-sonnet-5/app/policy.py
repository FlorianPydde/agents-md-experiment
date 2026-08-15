"""Policy: decides whether a step may run on its own or needs approval.

The first matching rule wins. This module holds the rule table both as
executable code (`decide`) and as a data description (`RULES`) so the same
rules can be reported over the HTTP API and in `show`/`export` without
duplicating the logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

CREDIT_AUTO_LIMIT = Decimal("100.00")

# A human-readable description of the rule table, in evaluation order.
# This is exposed as-is via the HTTP API's policy endpoint.
RULES: list[dict[str, str]] = [
    {"rule": "read operation", "outcome": "auto"},
    {"rule": "apply_credit of 100.00 or less", "outcome": "auto"},
    {"rule": "apply_credit above 100.00", "outcome": "approval:finance"},
    {"rule": "apply_debit", "outcome": "approval:finance"},
    {"rule": "freeze_account or unfreeze_account", "outcome": "approval:risk"},
    {"rule": "anything else", "outcome": "auto"},
]


@dataclass(frozen=True)
class PolicyDecision:
    """The outcome of evaluating policy for a single step."""

    requires_approval: bool
    role: str | None = None


def decide(operation: str, materiality: str, args: dict) -> PolicyDecision:
    """Evaluate the policy rules against a single planned step."""
    if materiality == "read":
        return PolicyDecision(requires_approval=False)

    if operation == "apply_credit":
        amount = args["amount"]
        if amount <= CREDIT_AUTO_LIMIT:
            return PolicyDecision(requires_approval=False)
        return PolicyDecision(requires_approval=True, role="finance")

    if operation == "apply_debit":
        return PolicyDecision(requires_approval=True, role="finance")

    if operation in ("freeze_account", "unfreeze_account"):
        return PolicyDecision(requires_approval=True, role="risk")

    return PolicyDecision(requires_approval=False)

"""Policy: decides whether a step may run on its own or needs approval.

The first matching rule wins:

1. A read operation runs on its own.
2. ``apply_credit`` of 100.00 or less runs on its own.
3. ``apply_credit`` above 100.00 needs ``finance``.
4. ``apply_debit`` needs ``finance``.
5. ``freeze_account`` and ``unfreeze_account`` need ``risk``.
6. Anything else runs on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models import MATERIALITY

AUTO_APPROVAL_CREDIT_LIMIT = Decimal("100.00")


@dataclass(frozen=True)
class PolicyDecision:
    auto: bool
    role: str | None = None

    @property
    def needs_approval(self) -> bool:
        return not self.auto


def evaluate(operation: str, amount: Decimal | None) -> PolicyDecision:
    """Evaluate policy for a step about to run.

    ``amount`` is the decimal amount tied to the step, when relevant
    (``apply_credit`` and ``apply_debit``), otherwise ``None``.
    """

    materiality = MATERIALITY[operation]

    # Rule 1: a read operation runs on its own.
    if materiality == "read":
        return PolicyDecision(auto=True)

    # Rule 2 & 3: apply_credit threshold.
    if operation == "apply_credit":
        assert amount is not None
        if amount <= AUTO_APPROVAL_CREDIT_LIMIT:
            return PolicyDecision(auto=True)
        return PolicyDecision(auto=False, role="finance")

    # Rule 4: apply_debit always needs finance.
    if operation == "apply_debit":
        return PolicyDecision(auto=False, role="finance")

    # Rule 5: freeze/unfreeze need risk.
    if operation in ("freeze_account", "unfreeze_account"):
        return PolicyDecision(auto=False, role="risk")

    # Rule 6: anything else runs on its own.
    return PolicyDecision(auto=True)


def policy_rules() -> list[dict]:
    """A human readable description of the policy rules, in match order."""

    return [
        {"rule": "read operation", "auto": True, "role": None},
        {
            "rule": "apply_credit <= 100.00",
            "auto": True,
            "role": None,
        },
        {
            "rule": "apply_credit > 100.00",
            "auto": False,
            "role": "finance",
        },
        {"rule": "apply_debit", "auto": False, "role": "finance"},
        {
            "rule": "freeze_account / unfreeze_account",
            "auto": False,
            "role": "risk",
        },
        {"rule": "anything else", "auto": True, "role": None},
    ]

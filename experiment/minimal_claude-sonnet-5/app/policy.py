"""Policy: decides whether a step may run on its own or needs approval.

The first matching rule wins, per SPEC.md:

1. A read operation runs on its own.
2. apply_credit of 100.00 or less runs on its own.
3. apply_credit above 100.00 needs finance.
4. apply_debit needs finance.
5. freeze_account and unfreeze_account need risk.
6. Anything else runs on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain import (
    GOODWILL_CREDIT_AUTO_LIMIT,
    OPERATION_MATERIALITY,
    ApproverRole,
    Materiality,
    OperationName,
)


@dataclass(frozen=True)
class PolicyDecision:
    needs_approval: bool
    required_role: ApproverRole | None
    rule: str
    """A short label identifying which rule matched, for the log and reports."""


@dataclass(frozen=True)
class PolicyRule:
    label: str
    description: str


POLICY_RULES: tuple[PolicyRule, ...] = (
    PolicyRule("read-auto", "A read operation runs on its own."),
    PolicyRule("apply-credit-auto", "apply_credit of 100.00 or less runs on its own."),
    PolicyRule("apply-credit-finance", "apply_credit above 100.00 needs finance."),
    PolicyRule("apply-debit-finance", "apply_debit needs finance."),
    PolicyRule("freeze-risk", "freeze_account and unfreeze_account need risk."),
    PolicyRule("default-auto", "Anything else runs on its own."),
)


def decide(operation: OperationName, amount: Decimal) -> PolicyDecision:
    if OPERATION_MATERIALITY[operation] is Materiality.READ:
        return PolicyDecision(False, None, "read-auto")
    if operation is OperationName.APPLY_CREDIT:
        if amount <= GOODWILL_CREDIT_AUTO_LIMIT:
            return PolicyDecision(False, None, "apply-credit-auto")
        return PolicyDecision(True, ApproverRole.FINANCE, "apply-credit-finance")
    if operation is OperationName.APPLY_DEBIT:
        return PolicyDecision(True, ApproverRole.FINANCE, "apply-debit-finance")
    if operation in (OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT):
        return PolicyDecision(True, ApproverRole.RISK, "freeze-risk")
    return PolicyDecision(False, None, "default-auto")

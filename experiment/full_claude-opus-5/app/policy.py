"""Policy: whether a step may run on its own, or needs a named role's approval.

Rules are evaluated in order and the first match wins, mirroring the spec's
own numbered list.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from app.domain import ApproverRole, Materiality, Money, OperationName

GOODWILL_CREDIT_AUTONOMOUS_LIMIT = Money(Decimal("100.00"))


class PolicyDecision:
    """Base class for a policy outcome: either autonomous or needs approval."""


@dataclass(frozen=True)
class Autonomous(PolicyDecision):
    pass


@dataclass(frozen=True)
class NeedsApproval(PolicyDecision):
    role: ApproverRole


@dataclass(frozen=True)
class PolicyContext:
    operation: OperationName
    materiality: Materiality
    amount: Money


@dataclass(frozen=True)
class PolicyRule:
    matches: Callable[[PolicyContext], bool]
    decide: Callable[[PolicyContext], PolicyDecision]


_RULES: tuple[PolicyRule, ...] = (
    # 1. A read operation runs on its own.
    PolicyRule(
        matches=lambda ctx: ctx.materiality is Materiality.READ,
        decide=lambda ctx: Autonomous(),
    ),
    # 2. apply_credit of 100.00 or less runs on its own.
    PolicyRule(
        matches=lambda ctx: ctx.operation is OperationName.APPLY_CREDIT
        and ctx.amount.is_at_most(GOODWILL_CREDIT_AUTONOMOUS_LIMIT),
        decide=lambda ctx: Autonomous(),
    ),
    # 3. apply_credit above 100.00 needs finance.
    PolicyRule(
        matches=lambda ctx: ctx.operation is OperationName.APPLY_CREDIT,
        decide=lambda ctx: NeedsApproval(ApproverRole.FINANCE),
    ),
    # 4. apply_debit needs finance.
    PolicyRule(
        matches=lambda ctx: ctx.operation is OperationName.APPLY_DEBIT,
        decide=lambda ctx: NeedsApproval(ApproverRole.FINANCE),
    ),
    # 5. freeze_account and unfreeze_account need risk.
    PolicyRule(
        matches=lambda ctx: ctx.operation
        in (OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT),
        decide=lambda ctx: NeedsApproval(ApproverRole.RISK),
    ),
    # 6. Anything else runs on its own.
    PolicyRule(
        matches=lambda ctx: True,
        decide=lambda ctx: Autonomous(),
    ),
)


def decide(ctx: PolicyContext) -> PolicyDecision:
    for rule in _RULES:
        if rule.matches(ctx):
            return rule.decide(ctx)
    raise RuntimeError("no policy rule matched; rule 6 should always match")

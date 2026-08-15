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
    description: str
    matches: Callable[[PolicyContext], bool]
    decide: Callable[[PolicyContext], PolicyDecision]


_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(
        description="A read operation runs on its own.",
        matches=lambda ctx: ctx.materiality is Materiality.READ,
        decide=lambda ctx: Autonomous(),
    ),
    PolicyRule(
        description="apply_credit of 100.00 or less runs on its own.",
        matches=lambda ctx: ctx.operation is OperationName.APPLY_CREDIT
        and ctx.amount.is_at_most(GOODWILL_CREDIT_AUTONOMOUS_LIMIT),
        decide=lambda ctx: Autonomous(),
    ),
    PolicyRule(
        description="apply_credit above 100.00 needs finance.",
        matches=lambda ctx: ctx.operation is OperationName.APPLY_CREDIT,
        decide=lambda ctx: NeedsApproval(ApproverRole.FINANCE),
    ),
    PolicyRule(
        description="apply_debit needs finance.",
        matches=lambda ctx: ctx.operation is OperationName.APPLY_DEBIT,
        decide=lambda ctx: NeedsApproval(ApproverRole.FINANCE),
    ),
    PolicyRule(
        description="freeze_account and unfreeze_account need risk.",
        matches=lambda ctx: ctx.operation
        in (OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT),
        decide=lambda ctx: NeedsApproval(ApproverRole.RISK),
    ),
    PolicyRule(
        description="Anything else runs on its own.",
        matches=lambda ctx: True,
        decide=lambda ctx: Autonomous(),
    ),
)


def rules() -> tuple[PolicyRule, ...]:
    """The ordered policy rules themselves, for display (the API's /policy)."""
    return _RULES


def decide(ctx: PolicyContext) -> PolicyDecision:
    for rule in _RULES:
        if rule.matches(ctx):
            return rule.decide(ctx)
    raise RuntimeError("no policy rule matched; rule 6 should always match")

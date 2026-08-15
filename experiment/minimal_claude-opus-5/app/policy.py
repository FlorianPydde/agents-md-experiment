"""Policy: may a step run on its own, or must a named role approve it first?"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from .domain import Materiality, OperationCall, OperationName, Role
from .operations import OPERATIONS

CREDIT_THRESHOLD = Decimal("100.00")


@dataclass(frozen=True)
class PolicyRule:
    """One numbered rule. The first whose condition holds decides the step."""

    number: int
    description: str
    applies: Callable[[OperationCall], bool]
    required_role: Role | None


@dataclass(frozen=True)
class PolicyDecision:
    rule: PolicyRule
    required_role: Role | None

    @property
    def needs_approval(self) -> bool:
        return self.required_role is not None


def _is_read(call: OperationCall) -> bool:
    return OPERATIONS[call.operation].materiality is Materiality.READ


def _small_credit(call: OperationCall) -> bool:
    return call.operation is OperationName.APPLY_CREDIT and call.amount <= CREDIT_THRESHOLD


def _large_credit(call: OperationCall) -> bool:
    return call.operation is OperationName.APPLY_CREDIT and call.amount > CREDIT_THRESHOLD


def _is_debit(call: OperationCall) -> bool:
    return call.operation is OperationName.APPLY_DEBIT


def _is_freeze_change(call: OperationCall) -> bool:
    return call.operation in (OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT)


RULES: tuple[PolicyRule, ...] = (
    PolicyRule(1, "a read operation runs on its own", _is_read, None),
    PolicyRule(2, "apply_credit of 100.00 or less runs on its own", _small_credit, None),
    PolicyRule(3, "apply_credit above 100.00 needs finance", _large_credit, Role.FINANCE),
    PolicyRule(4, "apply_debit needs finance", _is_debit, Role.FINANCE),
    PolicyRule(
        5, "freeze_account and unfreeze_account need risk", _is_freeze_change, Role.RISK
    ),
    PolicyRule(6, "anything else runs on its own", lambda _call: True, None),
)


def evaluate(call: OperationCall) -> PolicyDecision:
    for rule in RULES:
        if rule.applies(call):
            return PolicyDecision(rule=rule, required_role=rule.required_role)
    raise AssertionError("rule 6 always matches")

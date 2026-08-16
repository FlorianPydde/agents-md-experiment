"""Policy: may a step run on its own, or which role must approve it first."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from .domain import Materiality, OperationName, Role
from .operations import operation_for

AUTO_APPROVAL_LIMIT = Decimal("100.00")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The outcome of policy for one step."""

    rule: str
    required_role: Role | None

    @property
    def runs_alone(self) -> bool:
        return self.required_role is None


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One rule. The first rule whose test matches decides."""

    name: str
    describes: str
    test: Callable[[OperationName, Decimal], bool]
    required_role: Role | None


def _is_read(operation: OperationName, amount: Decimal) -> bool:
    return operation_for(operation).materiality is Materiality.READ


def _small_credit(operation: OperationName, amount: Decimal) -> bool:
    return operation is OperationName.APPLY_CREDIT and amount <= AUTO_APPROVAL_LIMIT


def _large_credit(operation: OperationName, amount: Decimal) -> bool:
    return operation is OperationName.APPLY_CREDIT and amount > AUTO_APPROVAL_LIMIT


def _is_debit(operation: OperationName, amount: Decimal) -> bool:
    return operation is OperationName.APPLY_DEBIT


def _is_freeze_change(operation: OperationName, amount: Decimal) -> bool:
    return operation in (OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT)


def _always(operation: OperationName, amount: Decimal) -> bool:
    return True


RULES: Sequence[PolicyRule] = (
    PolicyRule("read_runs_alone", "A read operation runs on its own.", _is_read, None),
    PolicyRule(
        "small_credit_runs_alone",
        "apply_credit of 100.00 or less runs on its own.",
        _small_credit,
        None,
    ),
    PolicyRule(
        "large_credit_needs_finance",
        "apply_credit above 100.00 needs finance.",
        _large_credit,
        Role.FINANCE,
    ),
    PolicyRule("debit_needs_finance", "apply_debit needs finance.", _is_debit, Role.FINANCE),
    PolicyRule(
        "freeze_change_needs_risk",
        "freeze_account and unfreeze_account need risk.",
        _is_freeze_change,
        Role.RISK,
    ),
    PolicyRule("default_runs_alone", "Anything else runs on its own.", _always, None),
)


def evaluate(operation: OperationName, amount: Decimal) -> PolicyDecision:
    """Apply the rules in order. The first match wins."""
    for rule in RULES:
        if rule.test(operation, amount):
            return PolicyDecision(rule=rule.name, required_role=rule.required_role)
    raise AssertionError("the last rule always matches")

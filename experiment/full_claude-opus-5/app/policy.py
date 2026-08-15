"""Policy: whether a step runs alone or needs a named role. First match wins."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .domain import PolicyVerdict
from .enums import Materiality, OperationName, Role
from .operations import OPERATIONS, OperationArgs
from .values import Money

CREDIT_SELF_SERVE_LIMIT = Money.parse("100.00")


@dataclass(frozen=True)
class PolicyRule:
    """One ordered rule: a description, a test, and the verdict it yields."""

    description: str
    matches: Callable[[OperationName, OperationArgs], bool]
    required_role: Role | None

    def verdict(self) -> PolicyVerdict:
        return PolicyVerdict(self.description, self.required_role)


def _is_read(operation: OperationName, args: OperationArgs) -> bool:
    return OPERATIONS[operation].materiality is Materiality.READ


def _is_small_credit(operation: OperationName, args: OperationArgs) -> bool:
    return operation is OperationName.APPLY_CREDIT and not (
        args.require_amount().exceeds(CREDIT_SELF_SERVE_LIMIT)
    )


def _is_large_credit(operation: OperationName, args: OperationArgs) -> bool:
    return operation is OperationName.APPLY_CREDIT


def _is_debit(operation: OperationName, args: OperationArgs) -> bool:
    return operation is OperationName.APPLY_DEBIT


def _is_freeze_change(operation: OperationName, args: OperationArgs) -> bool:
    return operation in (
        OperationName.FREEZE_ACCOUNT,
        OperationName.UNFREEZE_ACCOUNT,
    )


def _always(operation: OperationName, args: OperationArgs) -> bool:
    return True


RULES: tuple[PolicyRule, ...] = (
    PolicyRule("a read operation runs on its own", _is_read, None),
    PolicyRule(
        f"apply_credit of {CREDIT_SELF_SERVE_LIMIT} or less runs on its own",
        _is_small_credit,
        None,
    ),
    PolicyRule(
        f"apply_credit above {CREDIT_SELF_SERVE_LIMIT} needs finance",
        _is_large_credit,
        Role.FINANCE,
    ),
    PolicyRule("apply_debit needs finance", _is_debit, Role.FINANCE),
    PolicyRule(
        "freeze_account and unfreeze_account need risk",
        _is_freeze_change,
        Role.RISK,
    ),
    PolicyRule("anything else runs on its own", _always, None),
)


def decide(operation: OperationName, args: OperationArgs) -> PolicyVerdict:
    for rule in RULES:
        if rule.matches(operation, args):
            return rule.verdict()
    raise RuntimeError("policy has no catch all rule")

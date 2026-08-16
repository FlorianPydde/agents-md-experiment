"""Policy. The first matching rule decides whether a step may run on its own."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.domain import Money
from app.enums import Materiality, OperationName, Role
from app.operations import OPERATIONS, AmountArgs, StepArgs

SELF_SERVICE_CREDIT_LIMIT = Money(Decimal("100.00"))


@dataclass(frozen=True)
class PolicyQuery:
    """What policy is asked about: an operation and the arguments it would run with."""

    operation: OperationName
    args: StepArgs

    @property
    def materiality(self) -> Materiality:
        return OPERATIONS[self.operation].materiality

    @property
    def amount(self) -> Money | None:
        return self.args.amount if isinstance(self.args, AmountArgs) else None


def is_read(query: PolicyQuery) -> bool:
    return query.materiality is Materiality.READ


def is_small_credit(query: PolicyQuery) -> bool:
    return (
        query.operation is OperationName.APPLY_CREDIT
        and query.amount is not None
        and SELF_SERVICE_CREDIT_LIMIT.covers(query.amount)
    )


def is_credit(query: PolicyQuery) -> bool:
    return query.operation is OperationName.APPLY_CREDIT


def is_debit(query: PolicyQuery) -> bool:
    return query.operation is OperationName.APPLY_DEBIT


def is_freezing(query: PolicyQuery) -> bool:
    return query.operation in (
        OperationName.FREEZE_ACCOUNT,
        OperationName.UNFREEZE_ACCOUNT,
    )


def anything(query: PolicyQuery) -> bool:
    return True


@dataclass(frozen=True)
class PolicyRule:
    """One rule: what it says, who it demands, and when it applies."""

    describe: str
    required_role: Role | None
    applies: Callable[[PolicyQuery], bool]


POLICY_RULES: tuple[PolicyRule, ...] = (
    PolicyRule("a read operation runs on its own", None, is_read),
    PolicyRule("apply_credit of 100.00 or less runs on its own", None, is_small_credit),
    PolicyRule("apply_credit above 100.00 needs finance", Role.FINANCE, is_credit),
    PolicyRule("apply_debit needs finance", Role.FINANCE, is_debit),
    PolicyRule("freeze_account and unfreeze_account need risk", Role.RISK, is_freezing),
    PolicyRule("anything else runs on its own", None, anything),
)


@dataclass(frozen=True)
class PolicyOutcome:
    """The verdict: a required role, or none when the step runs on its own."""

    rule: str
    required_role: Role | None

    @property
    def needs_approval(self) -> bool:
        return self.required_role is not None


def decide(query: PolicyQuery) -> PolicyOutcome:
    for rule in POLICY_RULES:
        if rule.applies(query):
            return PolicyOutcome(rule.describe, rule.required_role)
    raise RuntimeError(f"No policy rule matched {query.operation}")

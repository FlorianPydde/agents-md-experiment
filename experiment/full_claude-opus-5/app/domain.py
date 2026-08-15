"""Domain enums, value objects, and entities.

These types have no framework dependency and enforce their own invariants
at construction time (rule 16). Wire (untrusted) data is parsed into these
types at the edge; nothing downstream sees raw strings or dicts describing
domain records (rules 6, 7, 9).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class Tier(StrEnum):
    STANDARD = "standard"
    PREMIUM = "premium"


class RequestKind(StrEnum):
    GOODWILL_CREDIT = "goodwill_credit"
    ACCOUNT_RECOVERY = "account_recovery"
    COLLECT_DEBT = "collect_debt"


class Origin(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class RequestState(StrEnum):
    RECEIVED = "received"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class OperationName(StrEnum):
    READ_ACCOUNT = "read_account"
    APPLY_CREDIT = "apply_credit"
    APPLY_DEBIT = "apply_debit"
    FREEZE_ACCOUNT = "freeze_account"
    UNFREEZE_ACCOUNT = "unfreeze_account"
    NOTIFY_CUSTOMER = "notify_customer"


class Materiality(StrEnum):
    READ = "read"
    WRITE = "write"


class ApproverRole(StrEnum):
    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


class StepStatus(StrEnum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"


class DomainError(ValueError):
    """Raised whenever domain data or an operation violates an invariant."""


@dataclass(frozen=True)
class Money:
    """A non-negative decimal amount, always compared and stored exactly."""

    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise DomainError(f"amount cannot be negative: {self.amount}")

    @classmethod
    def parse(cls, raw: str) -> "Money":
        try:
            return cls(Decimal(raw))
        except InvalidOperation as exc:
            raise DomainError(f"amount is not a valid decimal: {raw!r}") from exc

    def plus(self, other: "Money") -> "Money":
        return Money(self.amount + other.amount)

    def minus(self, other: "Money") -> "Money":
        return Money(self.amount - other.amount)

    def is_at_most(self, other: "Money") -> bool:
        return self.amount <= other.amount

    def is_less_than(self, other: "Money") -> bool:
        return self.amount < other.amount

    def formatted(self) -> str:
        return f"{self.amount.quantize(Decimal('0.01'))}"


AccountId = str
Reference = str


@dataclass(frozen=True)
class Account:
    """An account acted on by requests. Immutable: mutation returns a copy."""

    id: AccountId
    owner: str
    tier: Tier
    balance: Money
    frozen: bool

    def credited(self, amount: Money) -> "Account":
        return replace(self, balance=self.balance.plus(amount))

    def debited(self, amount: Money) -> "Account":
        if self.balance.is_less_than(amount):
            raise DomainError(
                f"account {self.id} balance {self.balance.formatted()} "
                f"is below debit amount {amount.formatted()}"
            )
        return replace(self, balance=self.balance.minus(amount))

    def frozen_copy(self) -> "Account":
        return replace(self, frozen=True)

    def unfrozen_copy(self) -> "Account":
        return replace(self, frozen=False)


@dataclass(frozen=True)
class Requester:
    name: str
    role: str
    origin: Origin


@dataclass(frozen=True)
class ServiceRequest:
    reference: Reference
    kind: RequestKind
    account_id: AccountId
    amount: Money
    requester: Requester


@dataclass(frozen=True)
class PlannedStep:
    index: int
    operation: OperationName


@dataclass(frozen=True)
class Plan:
    steps: tuple[PlannedStep, ...]

    def step_at(self, index: int) -> PlannedStep | None:
        if 0 <= index < len(self.steps):
            return self.steps[index]
        return None

    def is_last(self, index: int) -> bool:
        return index == len(self.steps) - 1


@dataclass(frozen=True)
class PendingApproval:
    reference: Reference
    step_index: int
    operation: OperationName
    required_role: ApproverRole


@dataclass(frozen=True)
class DecideCommand:
    reference: Reference
    role: ApproverRole
    decision: Decision


@dataclass(frozen=True)
class Intake:
    request: ServiceRequest


@dataclass(frozen=True)
class Decide:
    command: DecideCommand


ScenarioStep = Intake | Decide


class LogEntryType(StrEnum):
    REQUEST_RECEIVED = "request_received"
    PLAN_CREATED = "plan_created"
    POLICY_DECIDED = "policy_decided"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    OPERATION_RAN = "operation_ran"
    OPERATION_FAILED = "operation_failed"
    REQUEST_FINALIZED = "request_finalized"


@dataclass(frozen=True)
class LogEntry:
    id: int
    reference: Reference
    entry_type: LogEntryType
    data: dict[str, str]


@dataclass(frozen=True)
class RequestRecord:
    request: ServiceRequest
    state: RequestState
    current_step_index: int
    seq: int


@dataclass(frozen=True)
class StepRecord:
    index: int
    operation: OperationName
    status: StepStatus


@dataclass(frozen=True)
class ApprovalRecord:
    id: int
    reference: Reference
    step_index: int
    operation: OperationName
    required_role: ApproverRole
    resolved: bool
    decision: Decision | None
    resolved_by_role: ApproverRole | None

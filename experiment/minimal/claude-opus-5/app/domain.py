"""The vocabulary of the system: fixed sets as enums, records as frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from .errors import InputError


class RequestKind(StrEnum):
    GOODWILL_CREDIT = "goodwill_credit"
    ACCOUNT_RECOVERY = "account_recovery"
    COLLECT_DEBT = "collect_debt"


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


class Origin(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class Tier(StrEnum):
    STANDARD = "standard"
    PREMIUM = "premium"


class Role(StrEnum):
    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RequestState(StrEnum):
    RECEIVED = "received"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class StepState(StrEnum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EventType(StrEnum):
    REQUEST_RECEIVED = "request_received"
    PLAN_CREATED = "plan_created"
    POLICY_DECIDED = "policy_decided"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    OPERATION_RAN = "operation_ran"
    OPERATION_FAILED = "operation_failed"
    REQUEST_FINISHED = "request_finished"


class ScenarioAction(StrEnum):
    INTAKE = "intake"
    DECIDE = "decide"


FINAL_STATES: frozenset[RequestState] = frozenset(
    {RequestState.COMPLETED, RequestState.REJECTED, RequestState.FAILED}
)


def parse_member[E: StrEnum](enum: type[E], raw: object, *, field: str) -> E:
    """Turn external text into a member of a fixed set, or explain why it cannot."""
    if not isinstance(raw, str):
        raise InputError(f"{field} must be text, got {type(raw).__name__}")
    try:
        return enum(raw)
    except ValueError:
        allowed = ", ".join(member.value for member in enum)
        raise InputError(f"{field} must be one of: {allowed}. Got {raw!r}") from None


@dataclass(frozen=True, slots=True)
class Requester:
    name: str
    role: str
    origin: Origin


@dataclass(frozen=True, slots=True)
class ServiceRequest:
    reference: str
    kind: RequestKind
    account: str
    amount: Decimal
    requester: Requester


@dataclass(frozen=True, slots=True)
class DecisionEntry:
    reference: str
    role: Role
    decision: Decision


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    owner: str
    tier: Tier
    balance: Decimal
    frozen: bool

    def with_balance(self, balance: Decimal) -> Account:
        return replace(self, balance=balance)

    def with_frozen(self, frozen: bool) -> Account:
        return replace(self, frozen=frozen)


@dataclass(frozen=True, slots=True)
class Step:
    """One planned operation with the free form arguments the plan supplied."""

    index: int
    operation: OperationName
    arguments: dict[str, str]
    state: StepState = StepState.PENDING
    required_role: Role | None = None

    def with_state(self, state: StepState) -> Step:
        return replace(self, state=state)


@dataclass(frozen=True, slots=True)
class Approval:
    reference: str
    step_index: int
    operation: OperationName
    role: Role
    state: ApprovalState = ApprovalState.PENDING


@dataclass(frozen=True, slots=True)
class Event:
    sequence: int
    reference: str
    type: EventType
    details: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """A request, its plan, and where the run has reached."""

    request: ServiceRequest
    steps: tuple[Step, ...]
    state: RequestState
    arrival: int

    def with_state(self, state: RequestState) -> RequestRecord:
        return replace(self, state=state)

    def with_step(self, step: Step) -> RequestRecord:
        steps = list(self.steps)
        steps[step.index] = step
        return replace(self, steps=tuple(steps))

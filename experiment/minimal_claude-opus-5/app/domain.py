"""The vocabulary of the domain: closed sets as enums, records as frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Tier(StrEnum):
    STANDARD = "standard"
    PREMIUM = "premium"


class Origin(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class RequestKind(StrEnum):
    GOODWILL_CREDIT = "goodwill_credit"
    ACCOUNT_RECOVERY = "account_recovery"
    COLLECT_DEBT = "collect_debt"


class RequestState(StrEnum):
    RECEIVED = "received"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


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


class Role(StrEnum):
    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class StepState(StrEnum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    REJECTED = "rejected"
    FAILED = "failed"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ScenarioAction(StrEnum):
    INTAKE = "intake"
    DECIDE = "decide"


class EventKind(StrEnum):
    REQUEST_RECEIVED = "request_received"
    PLAN_CREATED = "plan_created"
    POLICY_DECIDED = "policy_decided"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    OPERATION_RAN = "operation_ran"
    OPERATION_FAILED = "operation_failed"
    REQUEST_FINALISED = "request_finalised"


@dataclass(frozen=True)
class Account:
    id: str
    owner: str
    tier: Tier
    balance: Decimal
    frozen: bool


@dataclass(frozen=True)
class Requester:
    name: str
    role: str
    origin: Origin


@dataclass(frozen=True)
class ServiceRequest:
    reference: str
    kind: RequestKind
    account: str
    amount: Decimal
    requester: Requester


@dataclass(frozen=True)
class DecisionEntry:
    reference: str
    role: Role
    decision: Decision


@dataclass(frozen=True)
class OperationCall:
    """A planned invocation: the operation together with the arguments it needs."""

    operation: OperationName
    account: str
    amount: Decimal
    origin: Origin


PLANS: dict[RequestKind, tuple[OperationName, ...]] = {
    RequestKind.GOODWILL_CREDIT: (
        OperationName.READ_ACCOUNT,
        OperationName.APPLY_CREDIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
    RequestKind.ACCOUNT_RECOVERY: (
        OperationName.READ_ACCOUNT,
        OperationName.UNFREEZE_ACCOUNT,
        OperationName.APPLY_CREDIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
    RequestKind.COLLECT_DEBT: (
        OperationName.READ_ACCOUNT,
        OperationName.APPLY_DEBIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
}


def plan_for(kind: RequestKind) -> tuple[OperationName, ...]:
    return PLANS[kind]

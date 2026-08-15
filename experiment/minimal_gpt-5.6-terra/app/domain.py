from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


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


class StepState(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
    REJECTED = "rejected"


class Operation(StrEnum):
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


class Origin(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


@dataclass(frozen=True)
class Account:
    id: str
    owner: str
    tier: str
    balance: Decimal
    frozen: bool


@dataclass(frozen=True)
class Requester:
    name: str
    role: str
    origin: Origin


@dataclass(frozen=True)
class Request:
    reference: str
    kind: RequestKind
    account: str
    amount: Decimal
    requester: Requester


@dataclass(frozen=True)
class Step:
    position: int
    operation: Operation
    state: StepState


@dataclass(frozen=True)
class PendingApproval:
    reference: str
    step_position: int
    required_role: Role


@dataclass(frozen=True)
class AccountArgs:
    account_id: str


@dataclass(frozen=True)
class AmountArgs:
    account_id: str
    amount: Decimal


@dataclass(frozen=True)
class NotifyArgs:
    account_id: str
    origin: Origin

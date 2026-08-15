from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class AccountTier(StrEnum):
    STANDARD = "standard"
    PREMIUM = "premium"


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
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


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


class ApprovalRole(StrEnum):
    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EventKind(StrEnum):
    REQUEST_RECEIVED = "request_received"
    PLAN_CREATED = "plan_created"
    POLICY_DECIDED = "policy_decided"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    OPERATION_RAN = "operation_ran"
    OPERATION_FAILED = "operation_failed"
    REQUEST_FINALIZED = "request_finalized"


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    TSV = "tsv"


@dataclass(frozen=True)
class Money:
    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount < Decimal("0"):
            raise ValueError("amount must not be negative")

    def display(self) -> str:
        return f"{self.amount:.2f}"


@dataclass(frozen=True)
class Account:
    account_id: str
    owner: str
    tier: AccountTier
    balance: Money
    frozen: bool

    def credited(self, amount: Money) -> Account:
        return Account(
            self.account_id,
            self.owner,
            self.tier,
            Money(self.balance.amount + amount.amount),
            self.frozen,
        )

    def debited(self, amount: Money) -> Account:
        if self.balance.amount < amount.amount:
            raise OperationError(
                f"account {self.account_id} has insufficient balance"
            )
        return Account(
            self.account_id,
            self.owner,
            self.tier,
            Money(self.balance.amount - amount.amount),
            self.frozen,
        )

    def with_frozen(self, frozen: bool) -> Account:
        return Account(
            self.account_id,
            self.owner,
            self.tier,
            self.balance,
            frozen,
        )


@dataclass(frozen=True)
class Requester:
    name: str
    role: str
    origin: Origin


@dataclass(frozen=True)
class ServiceRequest:
    reference: str
    kind: RequestKind
    account_id: str
    amount: Money
    requester: Requester


@dataclass(frozen=True)
class PolicyOutcome:
    required_role: ApprovalRole | None

    @property
    def automatic(self) -> bool:
        return self.required_role is None


class AppError(Exception):
    pass


class NotFoundError(AppError):
    pass


class ConflictError(AppError):
    pass


class OperationError(AppError):
    pass

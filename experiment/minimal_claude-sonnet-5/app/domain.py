"""Domain model for the Governed Service Request Runner.

Everything past the parsing edge (see `loader.py`) works with these types,
never with bare dicts or strings for values drawn from a fixed set.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.errors import AppError


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


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Role(StrEnum):
    """Roles that appear in requester data or in `decide` entries."""

    AGENT = "agent"
    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


class ApproverRole(StrEnum):
    """The subset of roles that may resolve a pending approval."""

    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


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


# Plans: the fixed, ordered list of operations for each request kind.
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

OPERATION_MATERIALITY: dict[OperationName, Materiality] = {
    OperationName.READ_ACCOUNT: Materiality.READ,
    OperationName.APPLY_CREDIT: Materiality.WRITE,
    OperationName.APPLY_DEBIT: Materiality.WRITE,
    OperationName.FREEZE_ACCOUNT: Materiality.WRITE,
    OperationName.UNFREEZE_ACCOUNT: Materiality.WRITE,
    OperationName.NOTIFY_CUSTOMER: Materiality.WRITE,
}

GOODWILL_CREDIT_AUTO_LIMIT = Decimal("100.00")


def parse_amount(raw: str, *, field: str = "amount") -> Decimal:
    """Parse a decimal amount string, rejecting malformed or negative values."""
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError) as exc:
        raise AppError(f"{field} is not a valid decimal amount: {raw!r}") from exc
    if value < 0:
        raise AppError(f"{field} must not be negative: {raw!r}")
    return value


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
    role: Role
    origin: Origin


@dataclass(frozen=True)
class ServiceRequest:
    reference: str
    kind: RequestKind
    account: str
    amount: Decimal
    requester: Requester


@dataclass(frozen=True)
class DecideEvent:
    reference: str
    role: ApproverRole
    decision: Decision


@dataclass(frozen=True)
class World:
    accounts: dict[str, Account]


@dataclass(frozen=True)
class Scenario:
    """An ordered sequence of intake and decide events."""

    events: tuple[ServiceRequest | DecideEvent, ...]

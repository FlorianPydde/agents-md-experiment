"""The vocabulary of the system: the fixed sets of values and the records that
move between the loader, the engine and the store."""

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


class Kind(StrEnum):
    GOODWILL_CREDIT = "goodwill_credit"
    ACCOUNT_RECOVERY = "account_recovery"
    COLLECT_DEBT = "collect_debt"


class State(StrEnum):
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


#: The ordered steps each kind of request expands into.
PLANS: dict[Kind, tuple[str, ...]] = {
    Kind.GOODWILL_CREDIT: ("read_account", "apply_credit", "notify_customer"),
    Kind.ACCOUNT_RECOVERY: (
        "read_account",
        "unfreeze_account",
        "apply_credit",
        "notify_customer",
    ),
    Kind.COLLECT_DEBT: ("read_account", "apply_debit", "notify_customer"),
}


@dataclass(slots=True)
class Account:
    id: str
    owner: str
    tier: Tier
    balance: Decimal
    frozen: bool


@dataclass(frozen=True, slots=True)
class Requester:
    name: str
    role: str
    origin: Origin


@dataclass(frozen=True, slots=True)
class Request:
    reference: str
    kind: Kind
    account: str
    amount: Decimal
    requester: Requester


@dataclass(frozen=True, slots=True)
class Step:
    index: int
    operation: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class Approval:
    reference: str
    step_index: int
    operation: str
    role: Role
    state: str
    decision: str | None

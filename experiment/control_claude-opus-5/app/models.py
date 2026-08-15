"""Domain vocabulary: the closed sets of values and the records built from them."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from .errors import InputError


class StrEnum(str, Enum):
    """An enum whose members compare and serialise as their string value."""

    def __str__(self) -> str:
        return self.value

    @classmethod
    def parse(cls, raw: Any, label: str) -> "StrEnum":
        if not isinstance(raw, str):
            raise InputError(f"{label} must be a string, got {type(raw).__name__}")
        try:
            return cls(raw)
        except ValueError:
            allowed = ", ".join(member.value for member in cls)
            raise InputError(f"{label} must be one of: {allowed} (got {raw!r})") from None


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


class Role(StrEnum):
    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Materiality(StrEnum):
    READ = "read"
    WRITE = "write"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


FINAL_STATES = frozenset({State.COMPLETED, State.REJECTED, State.FAILED})


def parse_amount(raw: Any, label: str) -> Decimal:
    """Parse a decimal amount written as a string, rejecting negatives."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise InputError(f"{label} must be a decimal amount written as a string (got {raw!r})")
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise InputError(f"{label} is not a valid decimal amount: {raw!r}") from None
    if not value.is_finite():
        raise InputError(f"{label} is not a finite decimal amount: {raw!r}")
    if value < 0:
        raise InputError(f"{label} must not be negative (got {raw!r})")
    return value


def require(mapping: Any, key: str, label: str) -> Any:
    """Fetch a required field from a mapping, or explain what is missing."""
    if not isinstance(mapping, dict):
        raise InputError(f"{label} must be an object, got {type(mapping).__name__}")
    if key not in mapping:
        raise InputError(f"{label} is missing the required field {key!r}")
    return mapping[key]


def require_str(mapping: Any, key: str, label: str) -> str:
    value = require(mapping, key, label)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label}.{key} must be a non empty string (got {value!r})")
    return value


@dataclass
class Account:
    id: str
    owner: str
    tier: Tier
    balance: Decimal
    frozen: bool

    @classmethod
    def from_json(cls, raw: Any, index: int) -> "Account":
        label = f"world.accounts[{index}]"
        frozen = require(raw, "frozen", label)
        if not isinstance(frozen, bool):
            raise InputError(f"{label}.frozen must be true or false (got {frozen!r})")
        return cls(
            id=require_str(raw, "id", label),
            owner=require_str(raw, "owner", label),
            tier=Tier.parse(require(raw, "tier", label), f"{label}.tier"),
            balance=parse_amount(require(raw, "balance", label), f"{label}.balance"),
            frozen=frozen,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner": self.owner,
            "tier": self.tier.value,
            "balance": format(self.balance, ".2f"),
            "frozen": self.frozen,
        }


@dataclass(frozen=True)
class Requester:
    name: str
    role: str
    origin: Origin

    @classmethod
    def from_json(cls, raw: Any, label: str) -> "Requester":
        return cls(
            name=require_str(raw, "name", label),
            role=require_str(raw, "role", label),
            origin=Origin.parse(require(raw, "origin", label), f"{label}.origin"),
        )

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role, "origin": self.origin.value}


@dataclass
class Step:
    index: int
    operation: str
    state: StepState = StepState.PENDING
    detail: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "operation": self.operation,
            "state": self.state.value,
            "detail": self.detail,
        }


@dataclass
class Approval:
    reference: str
    step_index: int
    operation: str
    required_role: Role
    state: ApprovalState = ApprovalState.PENDING
    decided_by: Role | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "step_index": self.step_index,
            "operation": self.operation,
            "required_role": self.required_role.value,
            "state": self.state.value,
            "decided_by": self.decided_by.value if self.decided_by else None,
        }


@dataclass
class Request:
    reference: str
    kind: Kind
    account: str
    amount: Decimal
    requester: Requester
    arrival: int
    state: State = State.RECEIVED
    steps: list[Step] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: Any, arrival: int, label: str) -> "Request":
        return cls(
            reference=require_str(raw, "reference", label),
            kind=Kind.parse(require(raw, "kind", label), f"{label}.kind"),
            account=require_str(raw, "account", label),
            amount=parse_amount(require(raw, "amount", label), f"{label}.amount"),
            requester=Requester.from_json(
                require(raw, "requester", label), f"{label}.requester"
            ),
            arrival=arrival,
        )

    @property
    def pending_approval(self) -> Approval | None:
        for approval in self.approvals:
            if approval.state is ApprovalState.PENDING:
                return approval
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "kind": self.kind.value,
            "account": self.account,
            "amount": format(self.amount, ".2f"),
            "requester": self.requester.to_json(),
            "state": self.state.value,
            "steps": [step.to_json() for step in self.steps],
            "approvals": [approval.to_json() for approval in self.approvals],
        }

"""Domain types. They enforce their own invariants at construction."""

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from app.enums import (
    ApprovalState,
    Decision,
    OperationName,
    Origin,
    RequestKind,
    RequestState,
    Role,
    StepState,
    Tier,
)
from app.errors import OperationFailure, ServiceRequestError

CENTS = Decimal("0.01")


@dataclass(frozen=True, order=True)
class Money:
    """A non negative amount of money, exact to the cent."""

    amount: Decimal

    def __post_init__(self) -> None:
        try:
            quantized = self.amount.quantize(CENTS)
        except InvalidOperation as exc:
            raise ValueError(f"{self.amount} is not a usable money amount") from exc
        if quantized < Decimal(0):
            raise ValueError(f"money cannot be negative, got {quantized}")
        object.__setattr__(self, "amount", quantized)

    @classmethod
    def parse(cls, text: str) -> "Money":
        try:
            return cls(Decimal(text))
        except InvalidOperation as exc:
            raise ValueError(f"{text!r} is not a decimal amount") from exc

    def plus(self, other: "Money") -> "Money":
        return Money(self.amount + other.amount)

    def minus(self, other: "Money") -> "Money":
        return Money(self.amount - other.amount)

    def covers(self, other: "Money") -> bool:
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"{self.amount:.2f}"


@dataclass(frozen=True)
class Account:
    """An account the system acts on."""

    id: str
    owner: str
    tier: Tier
    balance: Money
    frozen: bool

    def ensure_active(self) -> None:
        if self.frozen:
            raise OperationFailure(f"account {self.id} is frozen")

    def ensure_covers(self, amount: Money) -> None:
        if not self.balance.covers(amount):
            raise OperationFailure(
                f"account {self.id} balance {self.balance} is below {amount}"
            )

    def credited(self, amount: Money) -> "Account":
        return replace(self, balance=self.balance.plus(amount))

    def debited(self, amount: Money) -> "Account":
        return replace(self, balance=self.balance.minus(amount))

    def with_frozen(self, frozen: bool) -> "Account":
        return replace(self, frozen=frozen)

    def state_word(self) -> str:
        return "frozen" if self.frozen else "active"


@dataclass(frozen=True)
class Requester:
    """Who asked for the work. `role` here is free form intake data."""

    name: str
    role: str
    origin: Origin


@dataclass(frozen=True)
class ServiceRequest:
    """A request as it arrived from the intake system."""

    reference: str
    kind: RequestKind
    account: str
    amount: Money
    requester: Requester


@dataclass(frozen=True)
class TrackedRequest:
    """A request together with the state the runner has reached."""

    request: ServiceRequest
    state: RequestState


@dataclass(frozen=True)
class StepRecord:
    """A step of a plan as it stands in the store."""

    position: int
    operation: OperationName
    state: StepState


@dataclass(frozen=True)
class DecisionCommand:
    """A role resolving a pending approval."""


    reference: str
    role: Role
    decision: Decision


@dataclass(frozen=True)
class Approval:
    """An approval attached to one step of one request."""

    reference: str
    position: int
    role: Role
    state: ApprovalState

    def ensure_role(self, role: Role) -> None:
        if role is not self.role:
            raise ServiceRequestError(
                f"{self.reference} needs approval from {self.role}, not from {role}"
            )

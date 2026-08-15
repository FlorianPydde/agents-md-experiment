"""Domain types. Frozen dataclasses, no framework dependency."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .enums import (
    Decision,
    OperationName,
    Origin,
    RequestKind,
    RequestState,
    Role,
    StepState,
    Tier,
)
from .errors import AppError, OperationFailure
from .values import Money


@dataclass(frozen=True)
class Account:
    id: str
    owner: str
    tier: Tier
    balance: Money
    frozen: bool

    def ensure_writable(self, operation: OperationName) -> None:
        """Every write other than unfreeze_account is refused on a frozen account."""
        if self.frozen and operation is not OperationName.UNFREEZE_ACCOUNT:
            raise OperationFailure(f"account {self.id} is frozen")

    def credited(self, amount: Money) -> Account:
        return replace(self, balance=self.balance.plus(amount))

    def debited(self, amount: Money) -> Account:
        if not self.balance.covers(amount):
            raise OperationFailure(
                f"account {self.id} balance {self.balance} is below {amount}"
            )
        return replace(self, balance=self.balance.minus(amount))

    def frozen_mark(self, frozen: bool) -> Account:
        return replace(self, frozen=frozen)

    def describe(self) -> str:
        return (
            f"balance {self.balance}, tier {self.tier}, "
            f"{'frozen' if self.frozen else 'active'}"
        )

    def presentation(self) -> str:
        state = "frozen" if self.frozen else "active"
        return f"{self.id:<10}{self.balance.formatted():>10}  {state}"


class World:
    """The accounts the system acts on. Mutable state, so a class."""

    def __init__(self, accounts: tuple[Account, ...]) -> None:
        self._accounts: dict[str, Account] = {a.id: a for a in accounts}

    def get(self, account_id: str) -> Account:
        account = self._accounts.get(account_id)
        if account is None:
            raise OperationFailure(f"unknown account {account_id}")
        return account

    def put(self, account: Account) -> None:
        self._accounts[account.id] = account

    def sorted_accounts(self) -> tuple[Account, ...]:
        return tuple(self._accounts[k] for k in sorted(self._accounts))


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

    def presentation(self, state: RequestState) -> str:
        return f"{self.reference:<10}{self.kind:<20}{state}"


@dataclass(frozen=True)
class Step:
    index: int
    operation: OperationName
    state: StepState

    def with_state(self, state: StepState) -> Step:
        return replace(self, state=state)


@dataclass(frozen=True)
class Approval:
    reference: str
    step_index: int
    operation: OperationName
    required_role: Role
    resolution: Decision | None

    @property
    def is_pending(self) -> bool:
        return self.resolution is None

    def resolved(self, decision: Decision) -> Approval:
        return replace(self, resolution=decision)

    def ensure_role(self, role: Role) -> None:
        if role is not self.required_role:
            raise AppError(
                f"{self.reference} needs {self.required_role} approval, not {role}"
            )


@dataclass(frozen=True)
class OperationOutcome:
    """What running one operation produced: a note, and the account after it."""

    note: str
    account: Account


@dataclass(frozen=True)
class PolicyVerdict:
    rule: str
    required_role: Role | None

    @property
    def runs_alone(self) -> bool:
        return self.required_role is None


@dataclass(frozen=True)
class RequestRecord:
    """A request together with everything the system has decided about it."""

    request: ServiceRequest
    state: RequestState
    arrival: int
    steps: tuple[Step, ...]
    approvals: tuple[Approval, ...]

    def pending_approval(self) -> Approval | None:
        return next((a for a in self.approvals if a.is_pending), None)

    def next_index(self) -> int | None:
        return next(
            (s.index for s in self.steps if s.state is StepState.PENDING),
            None,
        )

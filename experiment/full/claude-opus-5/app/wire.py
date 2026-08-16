"""The edge. Untrusted JSON is parsed into wire models, then handed to the domain."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain import Account, DecisionCommand, Money, Requester, ServiceRequest
from app.enums import Decision, Origin, RequestKind, Role, Tier
from app.errors import ServiceRequestError


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountInput(Wire):
    id: str
    owner: str
    tier: Tier
    balance: Decimal
    frozen: bool

    def to_domain(self) -> Account:
        return Account(
            id=self.id,
            owner=self.owner,
            tier=self.tier,
            balance=Money(self.balance),
            frozen=self.frozen,
        )


class WorldInput(Wire):
    accounts: list[AccountInput]

    def to_domain(self) -> tuple[Account, ...]:
        return tuple(account.to_domain() for account in self.accounts)


class RequesterInput(Wire):
    name: str
    role: str
    origin: Origin

    def to_domain(self) -> Requester:
        return Requester(name=self.name, role=self.role, origin=self.origin)


class RequestInput(Wire):
    reference: str
    kind: RequestKind
    account: str
    amount: Decimal
    requester: RequesterInput

    def to_domain(self) -> ServiceRequest:
        return ServiceRequest(
            reference=self.reference,
            kind=self.kind,
            account=self.account,
            amount=Money(self.amount),
            requester=self.requester.to_domain(),
        )


class IntakeInput(Wire):
    action: Literal["intake"]
    request: RequestInput


class DecideInput(Wire):
    action: Literal["decide"]
    reference: str
    role: Role
    decision: Decision

    def to_domain(self) -> DecisionCommand:
        return DecisionCommand(
            reference=self.reference, role=self.role, decision=self.decision
        )


type ScenarioEntry = Annotated[IntakeInput | DecideInput, Field(discriminator="action")]


class ScenarioInput(Wire):
    steps: list[ScenarioEntry]


class DecisionBody(Wire):
    """The body of an HTTP request resolving an approval."""

    role: Role
    decision: Decision

    def to_domain(self, reference: str) -> DecisionCommand:
        return DecisionCommand(
            reference=reference, role=self.role, decision=self.decision
        )


def load_world(path: Path) -> tuple[Account, ...]:
    world = _parse(WorldInput, path)
    with _reporting(path):
        return world.to_domain()


def load_scenario(path: Path) -> tuple[ServiceRequest | DecisionCommand, ...]:
    scenario = _parse(ScenarioInput, path)
    with _reporting(path):
        return tuple(
            entry.request.to_domain()
            if isinstance(entry, IntakeInput)
            else entry.to_domain()
            for entry in scenario.steps
        )


@contextmanager
def _reporting(path: Path) -> Iterator[None]:
    """Turn a domain invariant violation into a message naming the file."""
    try:
        yield
    except ValueError as exc:
        raise ServiceRequestError(f"{path} is malformed: {exc}") from exc


def _parse[M: Wire](model: type[M], path: Path) -> M:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ServiceRequestError(f"{path} does not exist") from exc
    except OSError as exc:
        raise ServiceRequestError(f"{path} could not be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ServiceRequestError(f"{path} is not valid JSON: {exc.msg}") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ServiceRequestError(f"{path} is malformed: {_explain(exc)}") from exc
    except ValueError as exc:
        raise ServiceRequestError(f"{path} is malformed: {exc}") from exc


def _explain(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
        for problem in error.errors()
    )

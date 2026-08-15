"""Wire models: parse untrusted JSON at the edge into domain types (rules 6, 9, 10).

Nothing past `load_world` / `load_scenario` sees a raw dict or string that
describes a domain record.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError

from app.domain import (
    Account,
    AccountId,
    ApproverRole,
    Decide,
    DecideCommand,
    Decision,
    DomainError,
    Intake,
    Money,
    Origin,
    Requester,
    RequestKind,
    ScenarioStep,
    ServiceRequest,
    Tier,
)


class WireError(DomainError):
    """Raised when wire data fails to parse into domain types."""


class AccountWire(BaseModel):
    id: str
    owner: str
    tier: str
    balance: str
    frozen: bool

    def to_domain(self) -> Account:
        return Account(
            id=self.id,
            owner=self.owner,
            tier=Tier(self.tier),
            balance=Money.parse(self.balance),
            frozen=self.frozen,
        )


class WorldWire(BaseModel):
    accounts: list[AccountWire]

    def to_domain(self) -> dict[AccountId, Account]:
        accounts = [a.to_domain() for a in self.accounts]
        by_id = {a.id: a for a in accounts}
        if len(by_id) != len(accounts):
            raise WireError("duplicate account id in world file")
        return by_id


class RequesterWire(BaseModel):
    name: str
    role: str
    origin: str

    def to_domain(self) -> Requester:
        return Requester(name=self.name, role=self.role, origin=Origin(self.origin))


class IntakeRequestWire(BaseModel):
    reference: str
    kind: str
    account: str
    amount: str
    requester: RequesterWire

    def to_domain(self) -> ServiceRequest:
        return ServiceRequest(
            reference=self.reference,
            kind=RequestKind(self.kind),
            account_id=self.account,
            amount=Money.parse(self.amount),
            requester=self.requester.to_domain(),
        )


class IntakeEntryWire(BaseModel):
    action: Literal["intake"]
    request: IntakeRequestWire

    def to_domain(self) -> Intake:
        return Intake(request=self.request.to_domain())


class DecideEntryWire(BaseModel):
    action: Literal["decide"]
    reference: str
    role: str
    decision: str

    def to_domain(self) -> Decide:
        return Decide(
            command=DecideCommand(
                reference=self.reference,
                role=ApproverRole(self.role),
                decision=Decision(self.decision),
            )
        )


class ScenarioWire(BaseModel):
    steps: list[IntakeEntryWire | DecideEntryWire]

    def to_domain(self) -> list[ScenarioStep]:
        return [step.to_domain() for step in self.steps]


def load_world(raw: bytes) -> dict[AccountId, Account]:
    try:
        wire = WorldWire.model_validate_json(raw)
    except ValidationError as exc:
        raise WireError(f"malformed world file: {exc}") from exc
    try:
        return wire.to_domain()
    except ValueError as exc:
        raise WireError(f"invalid world data: {exc}") from exc


def load_scenario(raw: bytes) -> list[ScenarioStep]:
    try:
        wire = ScenarioWire.model_validate_json(raw)
    except ValidationError as exc:
        raise WireError(f"malformed scenario file: {exc}") from exc
    try:
        return wire.to_domain()
    except ValueError as exc:
        raise WireError(f"invalid scenario data: {exc}") from exc

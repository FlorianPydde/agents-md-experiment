"""Wire models. Untrusted JSON is parsed here and nowhere else."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .domain import Account, Requester, ServiceRequest, World
from .enums import Decision, Origin, RequestKind, Role, Tier
from .errors import AppError
from .values import Money


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccountInput(Strict):
    id: str
    owner: str
    tier: Tier
    balance: Decimal = Field(ge=0)
    frozen: bool

    def to_domain(self) -> Account:
        return Account(
            id=self.id,
            owner=self.owner,
            tier=self.tier,
            balance=Money(self.balance),
            frozen=self.frozen,
        )


class WorldInput(Strict):
    accounts: list[AccountInput]

    def to_domain(self) -> World:
        return World(tuple(a.to_domain() for a in self.accounts))


class RequesterInput(Strict):
    name: str
    role: str
    origin: Origin

    def to_domain(self) -> Requester:
        return Requester(name=self.name, role=self.role, origin=self.origin)


class ServiceRequestInput(Strict):
    reference: str
    kind: RequestKind
    account: str
    amount: Decimal = Field(ge=0)
    requester: RequesterInput

    def to_domain(self) -> ServiceRequest:
        return ServiceRequest(
            reference=self.reference,
            kind=self.kind,
            account_id=self.account,
            amount=Money(self.amount),
            requester=self.requester.to_domain(),
        )


class IntakeEntry(Strict):
    action: Literal["intake"]
    request: ServiceRequestInput


class DecideEntry(Strict):
    action: Literal["decide"]
    reference: str
    role: Role
    decision: Decision


ScenarioEntry = Annotated[IntakeEntry | DecideEntry, Field(discriminator="action")]


class ScenarioInput(Strict):
    steps: list[ScenarioEntry]


class DecisionBody(Strict):
    """Body of an HTTP approval resolution."""

    role: Role
    decision: Decision


def load_world(path: Path) -> World:
    return _load(path, WorldInput).to_domain()


def load_scenario(path: Path) -> ScenarioInput:
    return _load(path, ScenarioInput)


def _load[M: BaseModel](path: Path, model: type[M]) -> M:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AppError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AppError(f"{path} is not valid JSON: {exc.msg} at line {exc.lineno}") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise AppError(f"{path} is malformed: {_summarise(exc)}") from exc


def _summarise(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors()
    )

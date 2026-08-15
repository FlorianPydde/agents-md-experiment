from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import (
    Account,
    AccountTier,
    ApprovalRole,
    Decision,
    Money,
    Origin,
    RequestKind,
    Requester,
    ServiceRequest,
)

NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


class StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountInput(StrictWireModel):
    id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    tier: AccountTier
    balance: NonNegativeDecimal
    frozen: bool

    def to_domain(self) -> Account:
        return Account(
            account_id=self.id,
            owner=self.owner,
            tier=self.tier,
            balance=Money(self.balance),
            frozen=self.frozen,
        )


class WorldInput(StrictWireModel):
    accounts: list[AccountInput]

    @model_validator(mode="after")
    def unique_account_ids(self) -> WorldInput:
        account_ids = [account.id for account in self.accounts]
        if len(account_ids) != len(set(account_ids)):
            raise ValueError("account ids must be unique")
        return self


class RequesterInput(StrictWireModel):
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    origin: Origin

    def to_domain(self) -> Requester:
        return Requester(self.name, self.role, self.origin)


class RequestInput(StrictWireModel):
    reference: str = Field(min_length=1)
    kind: RequestKind
    account: str = Field(min_length=1)
    amount: NonNegativeDecimal
    requester: RequesterInput

    def to_domain(self) -> ServiceRequest:
        return ServiceRequest(
            reference=self.reference,
            kind=self.kind,
            account_id=self.account,
            amount=Money(self.amount),
            requester=self.requester.to_domain(),
        )


class IntakeInput(StrictWireModel):
    action: Literal["intake"]
    request: RequestInput


class DecideInput(StrictWireModel):
    action: Literal["decide"]
    reference: str = Field(min_length=1)
    role: ApprovalRole
    decision: Decision


ScenarioAction = Annotated[IntakeInput | DecideInput, Field(discriminator="action")]


class ScenarioInput(StrictWireModel):
    steps: list[ScenarioAction]


class DecisionInput(StrictWireModel):
    role: ApprovalRole
    decision: Decision

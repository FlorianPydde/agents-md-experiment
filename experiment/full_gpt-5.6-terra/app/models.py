from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


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


class ApprovalRole(StrEnum):
    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Origin(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class Tier(StrEnum):
    STANDARD = "standard"
    PREMIUM = "premium"


class Action(StrEnum):
    INTAKE = "intake"
    DECIDE = "decide"


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    TSV = "tsv"


class DomainError(ValueError):
    """An expected invalid domain action."""


@dataclass(frozen=True)
class Money:
    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount < Decimal("0"):
            raise DomainError("amount must not be negative")

    @classmethod
    def parse(cls, raw: str) -> Money:
        try:
            amount = Decimal(raw)
        except InvalidOperation as error:
            raise DomainError(f"invalid amount: {raw}") from error
        if not amount.is_finite():
            raise DomainError(f"invalid amount: {raw}")
        return cls(amount.quantize(Decimal("0.01")))

    def text(self) -> str:
        return f"{self.amount:.2f}"


@dataclass(frozen=True)
class Requester:
    name: str
    role: str
    origin: Origin


@dataclass(frozen=True)
class Account:
    identifier: str
    owner: str
    tier: Tier
    balance: Money
    frozen: bool


@dataclass(frozen=True)
class ServiceRequest:
    reference: str
    kind: RequestKind
    account: str
    amount: Money
    requester: Requester


class RequesterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    origin: Origin

    def to_domain(self) -> Requester:
        return Requester(name=self.name, role=self.role, origin=self.origin)


class RequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=1)
    kind: RequestKind
    account: str = Field(min_length=1)
    amount: str
    requester: RequesterInput

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: str) -> str:
        Money.parse(value)
        return value

    def to_domain(self) -> ServiceRequest:
        return ServiceRequest(
            reference=self.reference,
            kind=self.kind,
            account=self.account,
            amount=Money.parse(self.amount),
            requester=self.requester.to_domain(),
        )


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    tier: Tier
    balance: str
    frozen: bool

    @field_validator("balance")
    @classmethod
    def validate_balance(cls, value: str) -> str:
        Money.parse(value)
        return value

    def to_domain(self) -> Account:
        return Account(
            identifier=self.id,
            owner=self.owner,
            tier=self.tier,
            balance=Money.parse(self.balance),
            frozen=self.frozen,
        )


class WorldInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: list[AccountInput]


class IntakeStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    request: RequestInput

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: Action) -> Action:
        if value is not Action.INTAKE:
            raise ValueError("intake entry action must be intake")
        return value


class DecisionStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    reference: str = Field(min_length=1)
    role: ApprovalRole
    decision: Decision

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: Action) -> Action:
        if value is not Action.DECIDE:
            raise ValueError("decision entry action must be decide")
        return value


class ApprovalDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ApprovalRole
    decision: Decision

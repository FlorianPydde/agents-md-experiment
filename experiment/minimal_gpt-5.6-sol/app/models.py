from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import TypeVar


class AppError(Exception):
    """An expected error that is safe to show to a caller."""


class Tier(StrEnum):
    STANDARD = "standard"
    PREMIUM = "premium"


class Origin(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


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
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


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


class Role(StrEnum):
    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ScenarioAction(StrEnum):
    INTAKE = "intake"
    DECIDE = "decide"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    TSV = "tsv"


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
    role: str
    origin: Origin


@dataclass(frozen=True)
class ServiceRequest:
    reference: str
    kind: RequestKind
    account_id: str
    amount: Decimal
    requester: Requester


@dataclass(frozen=True)
class Intake:
    request: ServiceRequest


@dataclass(frozen=True)
class Decide:
    reference: str
    role: Role
    decision: Decision


ScenarioEntry = Intake | Decide


@dataclass(frozen=True)
class Step:
    id: int
    request_reference: str
    position: int
    operation: OperationName
    state: StepState


@dataclass(frozen=True)
class StoredRequest:
    request: ServiceRequest
    state: RequestState
    arrival_order: int


@dataclass(frozen=True)
class Approval:
    id: int
    request_reference: str
    step_id: int
    required_role: Role
    state: ApprovalState
    decided_by: Role | None
    decision: Decision | None


@dataclass(frozen=True)
class LogEntry:
    sequence: int
    request_reference: str
    event: str
    data: dict[str, object]


@dataclass(frozen=True)
class World:
    accounts: tuple[Account, ...]


E = TypeVar("E", bound=StrEnum)


def _record(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AppError(f"{context} must be an object")
    return value


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise AppError(f"{context} must be a list")
    return value


def _required(record: dict[str, object], field: str, context: str) -> object:
    if field not in record:
        raise AppError(f"{context} is missing required field '{field}'")
    return record[field]


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise AppError(f"{context} must be a non-empty string")
    return value


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise AppError(f"{context} must be true or false")
    return value


def _enum(enum_type: type[E], value: object, context: str) -> E:
    text = _text(value, context)
    try:
        return enum_type(text)
    except ValueError as error:
        choices = ", ".join(member.value for member in enum_type)
        raise AppError(f"{context} must be one of: {choices}") from error


def _amount(value: object, context: str) -> Decimal:
    text = _text(value, context)
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise AppError(f"{context} must be a decimal string") from error
    if not amount.is_finite():
        raise AppError(f"{context} must be finite")
    if amount < Decimal("0"):
        raise AppError(f"{context} must not be negative")
    return amount.quantize(Decimal("0.01"))


def _load_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError as error:
        raise AppError(f"file not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise AppError(f"cannot read {path}: {error}") from error


def load_world(path: Path) -> World:
    root = _record(_load_json(path), "world")
    raw_accounts = _list(_required(root, "accounts", "world"), "world.accounts")
    accounts: list[Account] = []
    seen: set[str] = set()
    for index, raw_account in enumerate(raw_accounts):
        context = f"world.accounts[{index}]"
        record = _record(raw_account, context)
        account_id = _text(_required(record, "id", context), f"{context}.id")
        if account_id in seen:
            raise AppError(f"duplicate account id: {account_id}")
        seen.add(account_id)
        accounts.append(
            Account(
                id=account_id,
                owner=_text(_required(record, "owner", context), f"{context}.owner"),
                tier=_enum(Tier, _required(record, "tier", context), f"{context}.tier"),
                balance=_amount(
                    _required(record, "balance", context), f"{context}.balance"
                ),
                frozen=_boolean(
                    _required(record, "frozen", context), f"{context}.frozen"
                ),
            )
        )
    return World(tuple(accounts))


def load_scenario(path: Path) -> tuple[ScenarioEntry, ...]:
    root = _record(_load_json(path), "scenario")
    raw_steps = _list(_required(root, "steps", "scenario"), "scenario.steps")
    entries: list[ScenarioEntry] = []
    for index, raw_step in enumerate(raw_steps):
        context = f"scenario.steps[{index}]"
        record = _record(raw_step, context)
        action = _enum(
            ScenarioAction, _required(record, "action", context), f"{context}.action"
        )
        if action is ScenarioAction.INTAKE:
            entries.append(Intake(_parse_request(record, context)))
        else:
            entries.append(
                Decide(
                    reference=_text(
                        _required(record, "reference", context),
                        f"{context}.reference",
                    ),
                    role=_enum(
                        Role, _required(record, "role", context), f"{context}.role"
                    ),
                    decision=_enum(
                        Decision,
                        _required(record, "decision", context),
                        f"{context}.decision",
                    ),
                )
            )
    return tuple(entries)


def _parse_request(record: dict[str, object], context: str) -> ServiceRequest:
    request_context = f"{context}.request"
    raw_request = _record(
        _required(record, "request", context),
        request_context,
    )
    requester_context = f"{request_context}.requester"
    raw_requester = _record(
        _required(raw_request, "requester", request_context),
        requester_context,
    )
    return ServiceRequest(
        reference=_text(
            _required(raw_request, "reference", request_context),
            f"{request_context}.reference",
        ),
        kind=_enum(
            RequestKind,
            _required(raw_request, "kind", request_context),
            f"{request_context}.kind",
        ),
        account_id=_text(
            _required(raw_request, "account", request_context),
            f"{request_context}.account",
        ),
        amount=_amount(
            _required(raw_request, "amount", request_context),
            f"{request_context}.amount",
        ),
        requester=Requester(
            name=_text(
                _required(raw_requester, "name", requester_context),
                f"{requester_context}.name",
            ),
            role=_text(
                _required(raw_requester, "role", requester_context),
                f"{requester_context}.role",
            ),
            origin=_enum(
                Origin,
                _required(raw_requester, "origin", requester_context),
                f"{requester_context}.origin",
            ),
        ),
    )

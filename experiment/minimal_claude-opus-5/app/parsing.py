"""The edge. Raw JSON becomes validated domain records here, and nowhere else."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from .domain import (
    Account,
    Decision,
    DecisionEntry,
    Origin,
    RequestKind,
    Requester,
    Role,
    ScenarioAction,
    ServiceRequest,
    Tier,
)
from .errors import AppError

E = TypeVar("E", bound=StrEnum)


def load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AppError(f"file not found: {path}") from None
    except OSError as exc:
        raise AppError(f"cannot read {path}: {exc.strerror}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppError(f"malformed JSON in {path}: {exc.msg} at line {exc.lineno}") from None


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError(f"{where}: expected an object")
    return value


def _field(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data:
        raise AppError(f"{where}: missing required field '{key}'")
    return data[key]


def _text(data: dict[str, Any], key: str, where: str) -> str:
    value = _field(data, key, where)
    if not isinstance(value, str) or not value:
        raise AppError(f"{where}: field '{key}' must be a non-empty string")
    return value


def _flag(data: dict[str, Any], key: str, where: str) -> bool:
    value = _field(data, key, where)
    if not isinstance(value, bool):
        raise AppError(f"{where}: field '{key}' must be true or false")
    return value


def _member(enum: type[E], data: dict[str, Any], key: str, where: str) -> E:
    raw = _text(data, key, where)
    try:
        return enum(raw)
    except ValueError:
        allowed = ", ".join(member.value for member in enum)
        raise AppError(f"{where}: '{key}' is '{raw}', expected one of: {allowed}") from None


def _amount(data: dict[str, Any], key: str, where: str) -> Decimal:
    raw = _field(data, key, where)
    if not isinstance(raw, str):
        raise AppError(f"{where}: field '{key}' must be a decimal amount written as a string")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise AppError(f"{where}: field '{key}' is not a valid decimal amount: '{raw}'") from None
    if not value.is_finite():
        raise AppError(f"{where}: field '{key}' is not a finite amount: '{raw}'")
    if value < 0:
        raise AppError(f"{where}: field '{key}' must not be negative: '{raw}'")
    return value


def parse_world(path: Path) -> tuple[Account, ...]:
    root = _mapping(load_json(path), str(path))
    raw_accounts = _field(root, "accounts", str(path))
    if not isinstance(raw_accounts, list):
        raise AppError(f"{path}: 'accounts' must be a list")
    accounts: list[Account] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_accounts):
        where = f"{path}: accounts[{index}]"
        data = _mapping(raw, where)
        account = Account(
            id=_text(data, "id", where),
            owner=_text(data, "owner", where),
            tier=_member(Tier, data, "tier", where),
            balance=_amount(data, "balance", where),
            frozen=_flag(data, "frozen", where),
        )
        if account.id in seen:
            raise AppError(f"{where}: duplicate account id '{account.id}'")
        seen.add(account.id)
        accounts.append(account)
    return tuple(accounts)


@dataclass(frozen=True)
class IntakeEntry:
    request: ServiceRequest


ScenarioEntry = IntakeEntry | DecisionEntry


def _parse_requester(data: dict[str, Any], where: str) -> Requester:
    raw = _mapping(_field(data, "requester", where), f"{where}.requester")
    inner = f"{where}.requester"
    return Requester(
        name=_text(raw, "name", inner),
        role=_text(raw, "role", inner),
        origin=_member(Origin, raw, "origin", inner),
    )


def _parse_intake(data: dict[str, Any], where: str) -> IntakeEntry:
    raw = _mapping(_field(data, "request", where), f"{where}.request")
    inner = f"{where}.request"
    return IntakeEntry(
        ServiceRequest(
            reference=_text(raw, "reference", inner),
            kind=_member(RequestKind, raw, "kind", inner),
            account=_text(raw, "account", inner),
            amount=_amount(raw, "amount", inner),
            requester=_parse_requester(raw, inner),
        )
    )


def _parse_decide(data: dict[str, Any], where: str) -> DecisionEntry:
    return DecisionEntry(
        reference=_text(data, "reference", where),
        role=_member(Role, data, "role", where),
        decision=_member(Decision, data, "decision", where),
    )


def parse_scenario(path: Path) -> tuple[ScenarioEntry, ...]:
    root = _mapping(load_json(path), str(path))
    raw_steps = _field(root, "steps", str(path))
    if not isinstance(raw_steps, list):
        raise AppError(f"{path}: 'steps' must be a list")
    entries: list[ScenarioEntry] = []
    for index, raw in enumerate(raw_steps):
        where = f"{path}: steps[{index}]"
        data = _mapping(raw, where)
        action = _member(ScenarioAction, data, "action", where)
        if action is ScenarioAction.INTAKE:
            entries.append(_parse_intake(data, where))
        else:
            entries.append(_parse_decide(data, where))
    return tuple(entries)

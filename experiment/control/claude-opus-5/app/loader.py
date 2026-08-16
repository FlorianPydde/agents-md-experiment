"""Reading and checking the two files that arrive from outside.

Every value is checked here so that the engine can assume it is working with
well formed data.  Anything unacceptable raises `AppError` with a message that
names the offending field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .errors import AppError
from .models import Account, Decision, Kind, Origin, Request, Requester, Role, Tier


@dataclass(frozen=True, slots=True)
class IntakeEvent:
    request: Request


@dataclass(frozen=True, slots=True)
class DecideEvent:
    reference: str
    role: Role
    decision: Decision


ScenarioEvent = IntakeEvent | DecideEvent


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AppError(f"file not found: {path}") from None
    except OSError as exc:
        raise AppError(f"cannot read {path}: {exc.strerror}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppError(f"{path} is not valid JSON: {exc.msg} at line {exc.lineno}") from None


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError(f"{where} must be an object")
    return value


def _require(data: dict[str, Any], field: str, where: str) -> Any:
    if field not in data or data[field] is None:
        raise AppError(f"{where} is missing required field '{field}'")
    return data[field]


def _text(data: dict[str, Any], field: str, where: str) -> str:
    value = _require(data, field, where)
    if not isinstance(value, str) or not value.strip():
        raise AppError(f"{where} field '{field}' must be a non empty string")
    return value


def _member(data: dict[str, Any], field: str, where: str, enum: type) -> Any:
    value = _text(data, field, where)
    try:
        return enum(value)
    except ValueError:
        allowed = ", ".join(sorted(member.value for member in enum))
        raise AppError(
            f"{where} field '{field}' is '{value}' but must be one of: {allowed}"
        ) from None


def parse_amount(value: Any, where: str) -> Decimal:
    """Turn a decimal amount written as a string into a `Decimal`."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AppError(f"{where} must be a decimal amount written as a string")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise AppError(f"{where} is '{value}' which is not a decimal amount") from None
    if not amount.is_finite():
        raise AppError(f"{where} is '{value}' which is not a finite amount")
    if amount < 0:
        raise AppError(f"{where} is '{value}' but amounts may not be negative")
    return amount


def load_world(path: Path) -> list[Account]:
    """Read the starting state of the accounts."""
    data = _mapping(_read_json(path), str(path))
    raw_accounts = _require(data, "accounts", str(path))
    if not isinstance(raw_accounts, list):
        raise AppError(f"{path} field 'accounts' must be a list")

    accounts: list[Account] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_accounts):
        where = f"{path} account {position}"
        entry = _mapping(raw, where)
        identifier = _text(entry, "id", where)
        if identifier in seen:
            raise AppError(f"{path} contains account '{identifier}' more than once")
        seen.add(identifier)
        frozen = _require(entry, "frozen", where)
        if not isinstance(frozen, bool):
            raise AppError(f"{where} field 'frozen' must be true or false")
        accounts.append(
            Account(
                id=identifier,
                owner=_text(entry, "owner", where),
                tier=_member(entry, "tier", where, Tier),
                balance=parse_amount(_require(entry, "balance", where), f"{where} field 'balance'"),
                frozen=frozen,
            )
        )
    if not accounts:
        raise AppError(f"{path} holds no accounts")
    return accounts


def _load_request(raw: Any, where: str) -> Request:
    entry = _mapping(raw, where)
    requester = _mapping(_require(entry, "requester", where), f"{where} field 'requester'")
    return Request(
        reference=_text(entry, "reference", where),
        kind=_member(entry, "kind", where, Kind),
        account=_text(entry, "account", where),
        amount=parse_amount(_require(entry, "amount", where), f"{where} field 'amount'"),
        requester=Requester(
            name=_text(requester, "name", f"{where} requester"),
            role=_text(requester, "role", f"{where} requester"),
            origin=_member(requester, "origin", f"{where} requester", Origin),
        ),
    )


def load_scenario(path: Path) -> list[ScenarioEvent]:
    """Read the ordered list of things that happen."""
    data = _mapping(_read_json(path), str(path))
    raw_steps = _require(data, "steps", str(path))
    if not isinstance(raw_steps, list):
        raise AppError(f"{path} field 'steps' must be a list")

    events: list[ScenarioEvent] = []
    references: set[str] = set()
    for position, raw in enumerate(raw_steps):
        where = f"{path} entry {position}"
        entry = _mapping(raw, where)
        action = _text(entry, "action", where)
        if action == "intake":
            request = _load_request(_require(entry, "request", where), f"{where} request")
            if request.reference in references:
                raise AppError(f"{path} intakes request '{request.reference}' more than once")
            references.add(request.reference)
            events.append(IntakeEvent(request=request))
        elif action == "decide":
            events.append(
                DecideEvent(
                    reference=_text(entry, "reference", where),
                    role=_member(entry, "role", where, Role),
                    decision=_member(entry, "decision", where, Decision),
                )
            )
        else:
            raise AppError(
                f"{where} field 'action' is '{action}' but must be one of: decide, intake"
            )
    return events

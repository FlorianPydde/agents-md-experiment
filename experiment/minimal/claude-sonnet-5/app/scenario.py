"""Loading and validating scenario.json, the ordered list of intake/decide
actions replayed by the `run` command."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.domain import ORIGINS, REQUEST_KINDS
from app.errors import AppError
from app.jsonio import read_json


@dataclass
class Requester:
    name: str
    role: str
    origin: str


@dataclass
class IntakeAction:
    reference: str
    kind: str
    account: str
    amount: Decimal
    requester: Requester


@dataclass
class DecideAction:
    reference: str
    role: str
    decision: str


def load_scenario(path: str | Path) -> list[IntakeAction | DecideAction]:
    data = read_json(path)
    if not isinstance(data, dict) or "steps" not in data:
        raise AppError(f"{path}: malformed scenario file, expected a 'steps' list")

    steps_raw = data["steps"]
    if not isinstance(steps_raw, list):
        raise AppError(f"{path}: 'steps' must be a list")

    actions: list[IntakeAction | DecideAction] = []
    for i, entry in enumerate(steps_raw):
        if not isinstance(entry, dict) or "action" not in entry:
            raise AppError(f"{path}: step {i} is missing an 'action'")
        action = entry["action"]
        if action == "intake":
            actions.append(_parse_intake(path, i, entry))
        elif action == "decide":
            actions.append(_parse_decide(path, i, entry))
        else:
            raise AppError(f"{path}: step {i} has unknown action '{action}'")

    return actions


def _require(path, i, entry, field):
    if field not in entry:
        raise AppError(f"{path}: step {i} is missing required field '{field}'")
    return entry[field]


def _parse_intake(path, i, entry) -> IntakeAction:
    request = _require(path, i, entry, "request")
    if not isinstance(request, dict):
        raise AppError(f"{path}: step {i} 'request' must be an object")

    reference = _require(path, i, request, "reference")
    kind = _require(path, i, request, "kind")
    if kind not in REQUEST_KINDS:
        raise AppError(f"{path}: step {i} has unknown kind '{kind}'")
    account = _require(path, i, request, "account")
    amount_raw = _require(path, i, request, "amount")
    try:
        amount = Decimal(amount_raw)
    except (InvalidOperation, TypeError):
        raise AppError(f"{path}: step {i} has a malformed amount") from None
    if amount < 0:
        raise AppError(f"{path}: step {i} has a negative amount")

    requester_raw = _require(path, i, request, "requester")
    if not isinstance(requester_raw, dict):
        raise AppError(f"{path}: step {i} 'requester' must be an object")
    for field in ("name", "role", "origin"):
        _require(path, i, requester_raw, field)
    origin = requester_raw["origin"]
    if origin not in ORIGINS:
        raise AppError(f"{path}: step {i} has unknown requester origin '{origin}'")

    requester = Requester(name=requester_raw["name"], role=requester_raw["role"], origin=origin)
    return IntakeAction(
        reference=reference, kind=kind, account=account, amount=amount, requester=requester
    )


def _parse_decide(path, i, entry) -> DecideAction:
    reference = _require(path, i, entry, "reference")
    role = _require(path, i, entry, "role")
    decision = _require(path, i, entry, "decision")
    if decision not in ("approve", "reject"):
        raise AppError(f"{path}: step {i} has unknown decision '{decision}'")
    return DecideAction(reference=reference, role=role, decision=decision)

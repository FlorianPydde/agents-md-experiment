"""Loads and validates `world.json` and `scenario.json` from the outside world.

This is the only place that reads raw JSON/dicts. Everything it produces is a
validated domain object from `app.domain`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain import (
    Account,
    DecideEvent,
    Decision,
    Origin,
    Requester,
    RequestKind,
    Role,
    Scenario,
    ServiceRequest,
    Tier,
    World,
    parse_amount,
)
from app.errors import AppError


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise AppError(f"file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AppError(f"could not read file: {path} ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppError(f"malformed JSON in {path}: {exc}") from exc


def _require(data: dict[str, Any], field: str, context: str) -> Any:
    if field not in data or data[field] is None:
        raise AppError(f"{context} is missing required field {field!r}")
    return data[field]


def _require_enum(enum_cls: type, value: Any, field: str, context: str) -> Any:
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(m.value for m in enum_cls)
        raise AppError(
            f"{context} has invalid {field}: {value!r} (allowed: {allowed})"
        ) from None


def load_world(path: Path) -> World:
    data = _read_json(path)
    if not isinstance(data, dict) or "accounts" not in data:
        raise AppError(f"{path} must be an object with an 'accounts' list")
    accounts_raw = data["accounts"]
    if not isinstance(accounts_raw, list):
        raise AppError(f"{path}: 'accounts' must be a list")

    accounts: dict[str, Account] = {}
    for entry in accounts_raw:
        if not isinstance(entry, dict):
            raise AppError(f"{path}: each account must be an object")
        account_id = _require(entry, "id", "account")
        owner = _require(entry, "owner", "account")
        tier = _require_enum(Tier, _require(entry, "tier", "account"), "tier", f"account {account_id!r}")
        balance = parse_amount(_require(entry, "balance", "account"), field=f"account {account_id!r} balance")
        frozen = _require(entry, "frozen", "account")
        if not isinstance(frozen, bool):
            raise AppError(f"account {account_id!r}: 'frozen' must be a boolean")
        if account_id in accounts:
            raise AppError(f"duplicate account id in {path}: {account_id!r}")
        accounts[account_id] = Account(
            id=account_id, owner=owner, tier=tier, balance=balance, frozen=frozen
        )
    return World(accounts=accounts)


def _load_requester(data: dict[str, Any], context: str) -> Requester:
    if not isinstance(data, dict):
        raise AppError(f"{context}: 'requester' must be an object")
    name = _require(data, "name", f"{context} requester")
    role = _require_enum(Role, _require(data, "role", f"{context} requester"), "role", f"{context} requester")
    origin = _require_enum(
        Origin, _require(data, "origin", f"{context} requester"), "origin", f"{context} requester"
    )
    return Requester(name=name, role=role, origin=origin)


def _load_intake(data: dict[str, Any]) -> ServiceRequest:
    reference = _require(data, "reference", "intake request")
    context = f"request {reference!r}"
    kind = _require_enum(RequestKind, _require(data, "kind", context), "kind", context)
    account = _require(data, "account", context)
    amount = parse_amount(_require(data, "amount", context), field=f"{context} amount")
    requester = _load_requester(_require(data, "requester", context), context)
    return ServiceRequest(
        reference=reference, kind=kind, account=account, amount=amount, requester=requester
    )


def _load_decide(data: dict[str, Any]) -> DecideEvent:
    reference = _require(data, "reference", "decide event")
    context = f"decide for {reference!r}"
    role = _require_enum(Role, _require(data, "role", context), "role", context)
    if role.value not in ("finance", "risk", "supervisor"):
        raise AppError(f"{context}: role {role.value!r} cannot approve or reject anything")
    decision = _require_enum(Decision, _require(data, "decision", context), "decision", context)
    from app.domain import ApproverRole

    return DecideEvent(reference=reference, role=ApproverRole(role.value), decision=decision)


def load_scenario(path: Path) -> Scenario:
    data = _read_json(path)
    if not isinstance(data, dict) or "steps" not in data:
        raise AppError(f"{path} must be an object with a 'steps' list")
    steps_raw = data["steps"]
    if not isinstance(steps_raw, list):
        raise AppError(f"{path}: 'steps' must be a list")

    events: list[ServiceRequest | DecideEvent] = []
    for i, entry in enumerate(steps_raw):
        if not isinstance(entry, dict):
            raise AppError(f"{path}: step {i} must be an object")
        action = entry.get("action")
        if action == "intake":
            request_data = _require(entry, "request", f"step {i}")
            if not isinstance(request_data, dict):
                raise AppError(f"step {i}: 'request' must be an object")
            events.append(_load_intake(request_data))
        elif action == "decide":
            events.append(_load_decide(entry))
        else:
            raise AppError(f"step {i}: invalid action {action!r} (allowed: intake, decide)")
    return Scenario(events=tuple(events))

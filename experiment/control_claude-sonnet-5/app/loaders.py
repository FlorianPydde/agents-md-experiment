"""Loading and validating the two external input files.

`world.json` describes the starting account state. `scenario.json` describes
the ordered sequence of intake and decide events to replay.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.domain import ACCOUNT_TIERS, DECISIONS, ORIGINS, REQUEST_KINDS, parse_amount
from app.errors import ValidationError


@dataclass(frozen=True)
class AccountSeed:
    id: str
    owner: str
    tier: str
    balance: Decimal
    frozen: bool


@dataclass(frozen=True)
class Requester:
    name: str
    role: str
    origin: str


@dataclass(frozen=True)
class IntakeRequest:
    reference: str
    kind: str
    account: str
    amount: Decimal
    requester: Requester


@dataclass(frozen=True)
class IntakeStep:
    request: IntakeRequest


@dataclass(frozen=True)
class DecideStep:
    reference: str
    role: str
    decision: str


def _load_json_file(path: Path) -> object:
    if not path.exists():
        raise ValidationError(f"file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"could not read file: {path} ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"malformed JSON in {path}: {exc}") from exc


def _require_field(data: dict, field: str, context: str) -> object:
    if field not in data or data[field] is None:
        raise ValidationError(f"{context} is missing required field '{field}'")
    return data[field]


def _require_str(data: dict, field: str, context: str) -> str:
    value = _require_field(data, field, context)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{context} field '{field}' must be a non-empty string")
    return value


def _require_bool(data: dict, field: str, context: str) -> bool:
    value = _require_field(data, field, context)
    if not isinstance(value, bool):
        raise ValidationError(f"{context} field '{field}' must be a boolean")
    return value


def load_world(path: Path) -> list[AccountSeed]:
    """Load and validate world.json into a list of account seeds."""
    data = _load_json_file(path)
    if not isinstance(data, dict) or "accounts" not in data:
        raise ValidationError(f"{path} must be an object with an 'accounts' list")
    accounts_raw = data["accounts"]
    if not isinstance(accounts_raw, list):
        raise ValidationError(f"{path}: 'accounts' must be a list")

    accounts: list[AccountSeed] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(accounts_raw):
        context = f"{path}: account[{i}]"
        if not isinstance(raw, dict):
            raise ValidationError(f"{context} must be an object")
        acc_id = _require_str(raw, "id", context)
        if acc_id in seen_ids:
            raise ValidationError(f"{context}: duplicate account id '{acc_id}'")
        seen_ids.add(acc_id)
        owner = _require_str(raw, "owner", context)
        tier = _require_str(raw, "tier", context)
        if tier not in ACCOUNT_TIERS:
            raise ValidationError(
                f"{context}: tier must be one of {ACCOUNT_TIERS}, got {tier!r}"
            )
        balance = parse_amount(_require_field(raw, "balance", context), field=f"{context}.balance")
        frozen = _require_bool(raw, "frozen", context)
        accounts.append(
            AccountSeed(id=acc_id, owner=owner, tier=tier, balance=balance, frozen=frozen)
        )
    return accounts


def _load_requester(raw: object, context: str) -> Requester:
    if not isinstance(raw, dict):
        raise ValidationError(f"{context}.requester must be an object")
    name = _require_str(raw, "name", f"{context}.requester")
    role = _require_str(raw, "role", f"{context}.requester")
    origin = _require_str(raw, "origin", f"{context}.requester")
    if origin not in ORIGINS:
        raise ValidationError(
            f"{context}.requester.origin must be one of {ORIGINS}, got {origin!r}"
        )
    return Requester(name=name, role=role, origin=origin)


def load_scenario(path: Path) -> list[IntakeStep | DecideStep]:
    """Load and validate scenario.json into an ordered list of steps."""
    data = _load_json_file(path)
    if not isinstance(data, dict) or "steps" not in data:
        raise ValidationError(f"{path} must be an object with a 'steps' list")
    steps_raw = data["steps"]
    if not isinstance(steps_raw, list):
        raise ValidationError(f"{path}: 'steps' must be a list")

    steps: list[IntakeStep | DecideStep] = []
    for i, raw in enumerate(steps_raw):
        context = f"{path}: step[{i}]"
        if not isinstance(raw, dict):
            raise ValidationError(f"{context} must be an object")
        action = _require_str(raw, "action", context)
        if action == "intake":
            req_raw = _require_field(raw, "request", context)
            if not isinstance(req_raw, dict):
                raise ValidationError(f"{context}.request must be an object")
            reference = _require_str(req_raw, "reference", f"{context}.request")
            kind = _require_str(req_raw, "kind", f"{context}.request")
            if kind not in REQUEST_KINDS:
                raise ValidationError(
                    f"{context}.request.kind must be one of {REQUEST_KINDS}, got {kind!r}"
                )
            account = _require_str(req_raw, "account", f"{context}.request")
            amount = parse_amount(
                _require_field(req_raw, "amount", f"{context}.request"),
                field=f"{context}.request.amount",
            )
            requester = _load_requester(
                _require_field(req_raw, "requester", f"{context}.request"),
                f"{context}.request",
            )
            steps.append(
                IntakeStep(
                    request=IntakeRequest(
                        reference=reference,
                        kind=kind,
                        account=account,
                        amount=amount,
                        requester=requester,
                    )
                )
            )
        elif action == "decide":
            reference = _require_str(raw, "reference", context)
            role = _require_str(raw, "role", context)
            decision = _require_str(raw, "decision", context)
            if decision not in DECISIONS:
                raise ValidationError(
                    f"{context}.decision must be one of {DECISIONS}, got {decision!r}"
                )
            steps.append(DecideStep(reference=reference, role=role, decision=decision))
        else:
            raise ValidationError(
                f"{context}.action must be 'intake' or 'decide', got {action!r}"
            )
    return steps

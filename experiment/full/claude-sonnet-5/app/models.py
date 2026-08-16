"""Data shapes for accounts, requests and scenario entries.

These are plain, validated views over the JSON documents that arrive from
outside the system (``world.json`` and ``scenario.json``). Validation happens
here so that malformed input is rejected early with a clear message.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.errors import AppError

VALID_TIERS = {"standard", "premium"}
VALID_KINDS = {"goodwill_credit", "account_recovery", "collect_debt"}
VALID_ORIGINS = {"internal", "external"}
VALID_DECISIONS = {"approve", "reject"}
VALID_ROLES = {"finance", "risk", "supervisor"}


def parse_amount(raw: Any, *, context: str) -> Decimal:
    """Parse a decimal amount from a string, rejecting negatives."""

    if not isinstance(raw, str):
        raise AppError(f"{context}: amount must be a string, got {raw!r}")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise AppError(f"{context}: amount {raw!r} is not a valid decimal") from exc
    if value < 0:
        raise AppError(f"{context}: amount {raw!r} must not be negative")
    return value


def _require_str(obj: dict, key: str, *, context: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise AppError(f"{context}: missing or invalid field '{key}'")
    return value


@dataclass(frozen=True)
class Account:
    id: str
    owner: str
    tier: str
    balance: Decimal
    frozen: bool

    @staticmethod
    def from_dict(raw: dict) -> "Account":
        context = "world.json account"
        account_id = _require_str(raw, "id", context=context)
        owner = _require_str(raw, "owner", context=context)
        tier = _require_str(raw, "tier", context=f"{context} {account_id}")
        if tier not in VALID_TIERS:
            raise AppError(
                f"account {account_id}: tier {tier!r} must be one of {sorted(VALID_TIERS)}"
            )
        if "balance" not in raw:
            raise AppError(f"account {account_id}: missing field 'balance'")
        balance = parse_amount(raw["balance"], context=f"account {account_id}")
        frozen = raw.get("frozen")
        if not isinstance(frozen, bool):
            raise AppError(f"account {account_id}: field 'frozen' must be a boolean")
        return Account(id=account_id, owner=owner, tier=tier, balance=balance, frozen=frozen)


@dataclass(frozen=True)
class Requester:
    name: str
    role: str
    origin: str

    @staticmethod
    def from_dict(raw: Any, *, context: str) -> "Requester":
        if not isinstance(raw, dict):
            raise AppError(f"{context}: missing or invalid field 'requester'")
        name = _require_str(raw, "name", context=context)
        role = _require_str(raw, "role", context=context)
        origin = _require_str(raw, "origin", context=context)
        if origin not in VALID_ORIGINS:
            raise AppError(
                f"{context}: requester origin {origin!r} must be one of {sorted(VALID_ORIGINS)}"
            )
        return Requester(name=name, role=role, origin=origin)


@dataclass(frozen=True)
class IntakeRequest:
    reference: str
    kind: str
    account: str
    amount: Decimal
    requester: Requester

    @staticmethod
    def from_dict(raw: dict) -> "IntakeRequest":
        if not isinstance(raw, dict):
            raise AppError("intake entry: missing or invalid field 'request'")
        reference = _require_str(raw, "reference", context="intake request")
        context = f"request {reference}"
        kind = _require_str(raw, "kind", context=context)
        if kind not in VALID_KINDS:
            raise AppError(f"{context}: kind {kind!r} must be one of {sorted(VALID_KINDS)}")
        account = _require_str(raw, "account", context=context)
        if "amount" not in raw:
            raise AppError(f"{context}: missing field 'amount'")
        amount = parse_amount(raw["amount"], context=context)
        requester = Requester.from_dict(raw.get("requester"), context=context)
        return IntakeRequest(
            reference=reference,
            kind=kind,
            account=account,
            amount=amount,
            requester=requester,
        )


@dataclass(frozen=True)
class DecideEntry:
    reference: str
    role: str
    decision: str

    @staticmethod
    def from_dict(raw: dict) -> "DecideEntry":
        reference = _require_str(raw, "reference", context="decide entry")
        context = f"decide entry for {reference}"
        role = _require_str(raw, "role", context=context)
        if role not in VALID_ROLES:
            raise AppError(f"{context}: role {role!r} must be one of {sorted(VALID_ROLES)}")
        decision = _require_str(raw, "decision", context=context)
        if decision not in VALID_DECISIONS:
            raise AppError(
                f"{context}: decision {decision!r} must be one of {sorted(VALID_DECISIONS)}"
            )
        return DecideEntry(reference=reference, role=role, decision=decision)


def load_world(raw: dict) -> list[Account]:
    if not isinstance(raw, dict) or "accounts" not in raw:
        raise AppError("world.json: missing field 'accounts'")
    accounts_raw = raw["accounts"]
    if not isinstance(accounts_raw, list):
        raise AppError("world.json: field 'accounts' must be a list")
    accounts = [Account.from_dict(entry) for entry in accounts_raw]
    seen: set[str] = set()
    for account in accounts:
        if account.id in seen:
            raise AppError(f"world.json: duplicate account id {account.id!r}")
        seen.add(account.id)
    return accounts


def load_scenario(raw: dict) -> list[tuple[str, Any]]:
    if not isinstance(raw, dict) or "steps" not in raw:
        raise AppError("scenario.json: missing field 'steps'")
    steps_raw = raw["steps"]
    if not isinstance(steps_raw, list):
        raise AppError("scenario.json: field 'steps' must be a list")
    entries: list[tuple[str, Any]] = []
    for index, entry in enumerate(steps_raw):
        if not isinstance(entry, dict):
            raise AppError(f"scenario.json: step {index} must be an object")
        action = entry.get("action")
        if action == "intake":
            entries.append(("intake", IntakeRequest.from_dict(entry.get("request"))))
        elif action == "decide":
            entries.append(("decide", DecideEntry.from_dict(entry)))
        else:
            raise AppError(f"scenario.json: step {index} has unknown action {action!r}")
    return entries

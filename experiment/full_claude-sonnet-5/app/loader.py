"""Loading world.json and scenario.json from disk, with clear error messages."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.errors import AppError
from app.operations import Account, World
from app.policy import ACCOUNT_TIERS


def load_json(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise AppError(f"file not found: {path}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise AppError(f"could not read file: {path} ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppError(f"file is not valid JSON: {path} ({exc})") from exc


def load_world(path: str) -> World:
    data = load_json(path)
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        raise AppError(f"world file {path} is missing an 'accounts' list")

    world = World()
    for entry in accounts:
        if not isinstance(entry, dict):
            raise AppError(f"world file {path} has a malformed account entry")
        account_id = entry.get("id")
        owner = entry.get("owner")
        tier = entry.get("tier")
        balance = entry.get("balance")
        frozen = entry.get("frozen")

        if not account_id:
            raise AppError(f"world file {path} has an account missing 'id'")
        if not owner:
            raise AppError(f"account {account_id} is missing 'owner'")
        if tier not in ACCOUNT_TIERS:
            raise AppError(f"account {account_id} has unknown tier: {tier!r}")
        if not isinstance(frozen, bool):
            raise AppError(f"account {account_id} has a non-boolean 'frozen' flag")
        try:
            balance_dec = Decimal(str(balance))
        except (InvalidOperation, TypeError):
            raise AppError(f"account {account_id} has an invalid balance: {balance!r}")
        if balance_dec < 0:
            raise AppError(f"account {account_id} has a negative balance: {balance}")

        if account_id in world.accounts:
            raise AppError(f"world file {path} has a duplicate account id: {account_id}")

        world.accounts[account_id] = Account(
            id=account_id, owner=owner, tier=tier, balance=balance_dec, frozen=frozen
        )

    return world


def load_scenario(path: str) -> list[dict]:
    data = load_json(path)
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise AppError(f"scenario file {path} is missing a 'steps' list")
    for entry in steps:
        if not isinstance(entry, dict) or "action" not in entry:
            raise AppError(f"scenario file {path} has a malformed entry")
        if entry["action"] not in ("intake", "decide"):
            raise AppError(f"scenario file {path} has an unknown action: {entry['action']!r}")
    return steps

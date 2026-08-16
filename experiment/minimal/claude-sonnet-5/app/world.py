"""Loading and validating world.json, the starting state of accounts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.errors import AppError
from app.jsonio import read_json
from app.operations import Account

REQUIRED_ACCOUNT_FIELDS = ("id", "owner", "tier", "balance", "frozen")
TIERS = ("standard", "premium")


def load_world(path: str | Path) -> dict[str, Account]:
    """Load world.json into a mapping of account id -> Account."""

    data = read_json(path)
    if not isinstance(data, dict) or "accounts" not in data:
        raise AppError(f"{path}: malformed world file, expected an 'accounts' list")

    accounts_raw = data["accounts"]
    if not isinstance(accounts_raw, list):
        raise AppError(f"{path}: 'accounts' must be a list")

    accounts: dict[str, Account] = {}
    for entry in accounts_raw:
        if not isinstance(entry, dict):
            raise AppError(f"{path}: each account must be an object")
        for field in REQUIRED_ACCOUNT_FIELDS:
            if field not in entry:
                raise AppError(f"{path}: account missing required field '{field}'")

        account_id = entry["id"]
        if account_id in accounts:
            raise AppError(f"{path}: duplicate account id '{account_id}'")

        tier = entry["tier"]
        if tier not in TIERS:
            raise AppError(f"{path}: account '{account_id}' has unknown tier '{tier}'")

        try:
            balance = Decimal(entry["balance"])
        except (InvalidOperation, TypeError):
            raise AppError(
                f"{path}: account '{account_id}' has a malformed balance"
            ) from None
        if balance < 0:
            raise AppError(f"{path}: account '{account_id}' has a negative balance")

        frozen = entry["frozen"]
        if not isinstance(frozen, bool):
            raise AppError(f"{path}: account '{account_id}' has a non boolean frozen flag")

        accounts[account_id] = Account(
            id=account_id,
            owner=entry["owner"],
            tier=tier,
            balance=balance,
            frozen=frozen,
        )

    return accounts

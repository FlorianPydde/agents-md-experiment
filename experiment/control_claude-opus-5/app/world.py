"""The mutable account state the operations act on."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import InputError, NotFoundError
from .models import Account


def load_json(path: Path, label: str) -> Any:
    """Read a JSON document, reporting missing or malformed files clearly."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise InputError(f"{label} file not found: {path}") from None
    except OSError as exc:
        raise InputError(f"{label} file could not be read: {path} ({exc.strerror})") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{label} file is not valid JSON: {path} (line {exc.lineno}: {exc.msg})") from None


class World:
    """Holds the accounts and applies balance and freeze changes to them."""

    def __init__(self, accounts: list[Account]) -> None:
        self._accounts: dict[str, Account] = {}
        for account in accounts:
            if account.id in self._accounts:
                raise InputError(f"world contains duplicate account id {account.id!r}")
            self._accounts[account.id] = account

    @classmethod
    def from_file(cls, path: Path) -> "World":
        raw = load_json(path, "world")
        if not isinstance(raw, dict) or "accounts" not in raw:
            raise InputError(f"world file must be an object with an 'accounts' list: {path}")
        accounts = raw["accounts"]
        if not isinstance(accounts, list):
            raise InputError(f"world.accounts must be a list: {path}")
        return cls([Account.from_json(item, index) for index, item in enumerate(accounts)])

    def account(self, account_id: str) -> Account:
        try:
            return self._accounts[account_id]
        except KeyError:
            raise NotFoundError(f"unknown account {account_id!r}") from None

    def sorted_accounts(self) -> list[Account]:
        return [self._accounts[key] for key in sorted(self._accounts)]

    def credit(self, account_id: str, amount: Decimal) -> Decimal:
        account = self.account(account_id)
        account.balance += amount
        return account.balance

    def debit(self, account_id: str, amount: Decimal) -> Decimal:
        account = self.account(account_id)
        account.balance -= amount
        return account.balance

    def set_frozen(self, account_id: str, frozen: bool) -> None:
        self.account(account_id).frozen = frozen

    def to_json(self) -> dict[str, Any]:
        return {"accounts": [account.to_json() for account in self.sorted_accounts()]}

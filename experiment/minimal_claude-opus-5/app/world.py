"""The mutable world the operations act on, held in memory during a run."""

from __future__ import annotations

from decimal import Decimal

from .domain import Account
from .errors import AppError


class World:
    """Accounts by id. The only place a balance or frozen flag changes."""

    def __init__(self, accounts: tuple[Account, ...]) -> None:
        self._accounts: dict[str, Account] = {a.id: a for a in accounts}

    def get(self, account_id: str) -> Account:
        account = self._accounts.get(account_id)
        if account is None:
            raise AppError(f"unknown account '{account_id}'")
        return account

    def _replace(self, account: Account) -> None:
        self._accounts[account.id] = account

    def credit(self, account_id: str, amount: Decimal) -> Account:
        account = self.get(account_id)
        updated = Account(
            id=account.id,
            owner=account.owner,
            tier=account.tier,
            balance=account.balance + amount,
            frozen=account.frozen,
        )
        self._replace(updated)
        return updated

    def debit(self, account_id: str, amount: Decimal) -> Account:
        account = self.get(account_id)
        updated = Account(
            id=account.id,
            owner=account.owner,
            tier=account.tier,
            balance=account.balance - amount,
            frozen=account.frozen,
        )
        self._replace(updated)
        return updated

    def set_frozen(self, account_id: str, frozen: bool) -> Account:
        account = self.get(account_id)
        updated = Account(
            id=account.id,
            owner=account.owner,
            tier=account.tier,
            balance=account.balance,
            frozen=frozen,
        )
        self._replace(updated)
        return updated

    def sorted_accounts(self) -> tuple[Account, ...]:
        return tuple(self._accounts[key] for key in sorted(self._accounts))

"""The world the system acts on: the accounts and the changes made to them."""

from __future__ import annotations

from decimal import Decimal

from .errors import AppError
from .models import Account


class World:
    """The accounts, held by id."""

    def __init__(self, accounts: list[Account]) -> None:
        self._accounts: dict[str, Account] = {account.id: account for account in accounts}

    def has(self, account_id: str) -> bool:
        return account_id in self._accounts

    def account(self, account_id: str) -> Account:
        try:
            return self._accounts[account_id]
        except KeyError:
            raise AppError(f"unknown account '{account_id}'") from None

    def accounts(self) -> list[Account]:
        """Every account, sorted by id."""
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

"""Supported operations: check + effect, plus materiality metadata.

Each operation is a small object exposing:
  - `name`: the operation identifier used in plans
  - `materiality`: "read" or "write"
  - `check(account, args)`: raises OperationFailure if the arguments/account
    state make the operation impossible to run
  - `run(account, args)`: performs the effect, returns a dict describing what
    happened (used for the log entry's data and for read reports)

`account` is a mutable in-memory Account object (see engine.py); operations
mutate it directly for write effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.domain import format_amount
from app.errors import OperationFailure

NOTIFY_TEMPLATES = {
    "internal": "Internal notice: your request has been processed.",
    "external": "Dear customer, your request has been processed.",
}


class Account(Protocol):
    id: str
    balance: Decimal
    tier: str
    frozen: bool


def _require_not_frozen(account, operation_name: str) -> None:
    if account.frozen:
        raise OperationFailure(f"account {account.id} is frozen; cannot run {operation_name}")


@dataclass(frozen=True)
class Operation:
    name: str
    materiality: str

    def check(self, account, args: dict) -> None:
        raise NotImplementedError

    def run(self, account, args: dict) -> dict:
        raise NotImplementedError


class ReadAccount(Operation):
    def __init__(self):
        super().__init__(name="read_account", materiality="read")

    def check(self, account, args: dict) -> None:
        return None

    def run(self, account, args: dict) -> dict:
        return {
            "balance": format_amount(account.balance),
            "tier": account.tier,
            "frozen": account.frozen,
        }


class ApplyCredit(Operation):
    def __init__(self):
        super().__init__(name="apply_credit", materiality="write")

    def check(self, account, args: dict) -> None:
        _require_not_frozen(account, self.name)

    def run(self, account, args: dict) -> dict:
        amount: Decimal = args["amount"]
        account.balance += amount
        return {"amount": format_amount(amount), "new_balance": format_amount(account.balance)}


class ApplyDebit(Operation):
    def __init__(self):
        super().__init__(name="apply_debit", materiality="write")

    def check(self, account, args: dict) -> None:
        _require_not_frozen(account, self.name)
        amount: Decimal = args["amount"]
        if account.balance < amount:
            raise OperationFailure(
                f"account {account.id} balance {format_amount(account.balance)} "
                f"is below debit amount {format_amount(amount)}"
            )

    def run(self, account, args: dict) -> dict:
        amount: Decimal = args["amount"]
        account.balance -= amount
        return {"amount": format_amount(amount), "new_balance": format_amount(account.balance)}


class FreezeAccount(Operation):
    def __init__(self):
        super().__init__(name="freeze_account", materiality="write")

    def check(self, account, args: dict) -> None:
        _require_not_frozen(account, self.name)

    def run(self, account, args: dict) -> dict:
        account.frozen = True
        return {"frozen": True}


class UnfreezeAccount(Operation):
    def __init__(self):
        super().__init__(name="unfreeze_account", materiality="write")

    def check(self, account, args: dict) -> None:
        # unfreeze_account is exempt from the frozen check by definition.
        return None

    def run(self, account, args: dict) -> dict:
        account.frozen = False
        return {"frozen": False}


class NotifyCustomer(Operation):
    def __init__(self):
        super().__init__(name="notify_customer", materiality="write")

    def check(self, account, args: dict) -> None:
        _require_not_frozen(account, self.name)
        origin = args["origin"]
        if origin not in NOTIFY_TEMPLATES:
            raise OperationFailure(f"unknown requester origin: {origin!r}")

    def run(self, account, args: dict) -> dict:
        origin = args["origin"]
        message = NOTIFY_TEMPLATES[origin]
        return {"origin": origin, "message": message}


OPERATIONS: dict[str, Operation] = {
    op.name: op
    for op in (
        ReadAccount(),
        ApplyCredit(),
        ApplyDebit(),
        FreezeAccount(),
        UnfreezeAccount(),
        NotifyCustomer(),
    )
}

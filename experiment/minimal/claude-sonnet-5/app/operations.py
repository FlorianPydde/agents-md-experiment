"""The six supported operations.

Each operation is a function `(account, args) -> result_data` that mutates the
in-memory `Account` and returns a small dict describing what happened, used
for logging. Failures are raised as `OperationError` with a clear message;
the engine turns those into a `failed` request state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain import MATERIALITY, NOTIFY_TEMPLATES


class OperationError(Exception):
    """Raised when an operation's check fails or it cannot complete."""


@dataclass
class Account:
    id: str
    owner: str
    tier: str
    balance: Decimal
    frozen: bool


def _require_not_frozen(operation: str, account: Account) -> None:
    if operation != "unfreeze_account" and MATERIALITY[operation] == "write" and account.frozen:
        raise OperationError(f"account {account.id} is frozen")


def read_account(account: Account, args: dict) -> dict:
    return {
        "account": account.id,
        "balance": str(account.balance),
        "tier": account.tier,
        "frozen": account.frozen,
    }


def apply_credit(account: Account, args: dict) -> dict:
    _require_not_frozen("apply_credit", account)
    amount = Decimal(args["amount"])
    account.balance += amount
    return {"account": account.id, "amount": str(amount), "balance": str(account.balance)}


def apply_debit(account: Account, args: dict) -> dict:
    _require_not_frozen("apply_debit", account)
    amount = Decimal(args["amount"])
    if account.balance < amount:
        raise OperationError(
            f"insufficient balance on {account.id}: has {account.balance}, needs {amount}"
        )
    account.balance -= amount
    return {"account": account.id, "amount": str(amount), "balance": str(account.balance)}


def freeze_account(account: Account, args: dict) -> dict:
    _require_not_frozen("freeze_account", account)
    account.frozen = True
    return {"account": account.id, "frozen": True}


def unfreeze_account(account: Account, args: dict) -> dict:
    account.frozen = False
    return {"account": account.id, "frozen": False}


def notify_customer(account: Account, args: dict) -> dict:
    _require_not_frozen("notify_customer", account)
    origin = args["origin"]
    message = NOTIFY_TEMPLATES[origin]
    return {"account": account.id, "origin": origin, "message": message}


OPERATION_FUNCS = {
    "read_account": read_account,
    "apply_credit": apply_credit,
    "apply_debit": apply_debit,
    "freeze_account": freeze_account,
    "unfreeze_account": unfreeze_account,
    "notify_customer": notify_customer,
}


def run_operation(operation: str, account: Account, args: dict) -> dict:
    """Run an operation, raising OperationError if it fails."""

    return OPERATION_FUNCS[operation](account, args)

"""The six supported operations: argument checks and effects.

Operations act on an in-memory `World` (accounts keyed by id) and never touch
timestamps or the database directly - the engine is responsible for logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.errors import AppError
from app.policy import NOTIFY_TEMPLATES, OPERATIONS, REQUESTER_ORIGINS


@dataclass
class Account:
    id: str
    owner: str
    tier: str
    balance: Decimal
    frozen: bool


@dataclass
class World:
    accounts: dict[str, Account] = field(default_factory=dict)

    def get(self, account_id: str) -> Account:
        account = self.accounts.get(account_id)
        if account is None:
            raise AppError(f"unknown account: {account_id!r}")
        return account


class OperationError(Exception):
    """Raised when an operation's arguments are invalid or its effect cannot happen.

    Distinct from AppError: this is caught by the engine and turned into a
    request 'failed' state plus a log entry, not a program exit.
    """


def _decimal(args: dict, key: str) -> Decimal:
    if key not in args or args[key] is None:
        raise OperationError(f"missing required argument '{key}'")
    try:
        value = Decimal(str(args[key]))
    except InvalidOperation as exc:
        raise OperationError(f"argument '{key}' is not a valid decimal: {args[key]!r}") from exc
    if value < 0:
        raise OperationError(f"argument '{key}' must not be negative")
    return value


def _account_id(args: dict) -> str:
    account_id = args.get("account")
    if not account_id:
        raise OperationError("missing required argument 'account'")
    return account_id


def check_read_account(world: World, args: dict) -> None:
    world.get(_account_id(args))


def run_read_account(world: World, args: dict) -> dict:
    account = world.get(_account_id(args))
    return {
        "account": account.id,
        "balance": str(account.balance),
        "tier": account.tier,
        "frozen": account.frozen,
    }


def check_apply_credit(world: World, args: dict) -> None:
    world.get(_account_id(args))
    _decimal(args, "amount")


def run_apply_credit(world: World, args: dict) -> dict:
    account = world.get(_account_id(args))
    if account.frozen:
        raise OperationError(f"account {account.id} is frozen")
    amount = _decimal(args, "amount")
    account.balance += amount
    return {"account": account.id, "amount": str(amount), "balance": str(account.balance)}


def check_apply_debit(world: World, args: dict) -> None:
    world.get(_account_id(args))
    _decimal(args, "amount")


def run_apply_debit(world: World, args: dict) -> dict:
    account = world.get(_account_id(args))
    if account.frozen:
        raise OperationError(f"account {account.id} is frozen")
    amount = _decimal(args, "amount")
    if account.balance < amount:
        raise OperationError(
            f"account {account.id} balance {account.balance} is below debit amount {amount}"
        )
    account.balance -= amount
    return {"account": account.id, "amount": str(amount), "balance": str(account.balance)}


def check_freeze_account(world: World, args: dict) -> None:
    world.get(_account_id(args))


def run_freeze_account(world: World, args: dict) -> dict:
    account = world.get(_account_id(args))
    if account.frozen:
        raise OperationError(f"account {account.id} is frozen")
    account.frozen = True
    return {"account": account.id, "frozen": True}


def check_unfreeze_account(world: World, args: dict) -> None:
    world.get(_account_id(args))


def run_unfreeze_account(world: World, args: dict) -> dict:
    account = world.get(_account_id(args))
    account.frozen = False
    return {"account": account.id, "frozen": False}


def check_notify_customer(world: World, args: dict) -> None:
    world.get(_account_id(args))
    origin = args.get("origin")
    if origin not in REQUESTER_ORIGINS:
        raise OperationError(f"unknown requester origin: {origin!r}")


def run_notify_customer(world: World, args: dict) -> dict:
    account = world.get(_account_id(args))
    if account.frozen:
        raise OperationError(f"account {account.id} is frozen")
    origin = args.get("origin")
    template = NOTIFY_TEMPLATES[origin]
    message = template.format(
        reference=args.get("reference", ""),
        account=account.id,
        owner=account.owner,
    )
    return {"account": account.id, "origin": origin, "message": message}


_CHECKS = {
    "read_account": check_read_account,
    "apply_credit": check_apply_credit,
    "apply_debit": check_apply_debit,
    "freeze_account": check_freeze_account,
    "unfreeze_account": check_unfreeze_account,
    "notify_customer": check_notify_customer,
}

_RUNS = {
    "read_account": run_read_account,
    "apply_credit": run_apply_credit,
    "apply_debit": run_apply_debit,
    "freeze_account": run_freeze_account,
    "unfreeze_account": run_unfreeze_account,
    "notify_customer": run_notify_customer,
}


def check(operation: str, world: World, args: dict) -> None:
    if operation not in OPERATIONS:
        raise OperationError(f"unknown operation: {operation!r}")
    _CHECKS[operation](world, args)


def execute(operation: str, world: World, args: dict) -> dict:
    if operation not in OPERATIONS:
        raise OperationError(f"unknown operation: {operation!r}")
    check(operation, world, args)
    return _RUNS[operation](world, args)

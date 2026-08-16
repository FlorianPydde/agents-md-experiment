"""The six supported operations: their materiality, checks and effects.

Each operation takes the current account state (a mutable dict with keys
``balance`` and ``frozen``) and the free form arguments carried on the plan
step, checks that the arguments are acceptable, and returns a small summary
of the effect it had. Operations raise :class:`OperationError` when they
cannot run.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from app.errors import OperationError

TWO_PLACES = Decimal("0.01")

NOTIFY_TEMPLATES = {
    "internal": "Internal notice: your request has been processed.",
    "external": "Dear customer, your request has been processed.",
}


@dataclass(frozen=True)
class OperationResult:
    """What happened when an operation ran."""

    summary: str
    data: dict[str, Any]


AccountState = dict[str, Any]


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES)


def check_read_account(account: AccountState, args: dict[str, Any]) -> None:
    return None


def effect_read_account(account: AccountState, args: dict[str, Any]) -> OperationResult:
    return OperationResult(
        summary=(
            f"balance={_quantize(account['balance'])} "
            f"tier={account['tier']} frozen={account['frozen']}"
        ),
        data={
            "balance": str(_quantize(account["balance"])),
            "tier": account["tier"],
            "frozen": account["frozen"],
        },
    )


def check_apply_credit(account: AccountState, args: dict[str, Any]) -> None:
    amount = args.get("amount")
    if not isinstance(amount, Decimal):
        raise OperationError("apply_credit: amount is required")
    if amount < 0:
        raise OperationError("apply_credit: amount must not be negative")
    if account["frozen"]:
        raise OperationError("apply_credit: account is frozen")


def effect_apply_credit(account: AccountState, args: dict[str, Any]) -> OperationResult:
    amount: Decimal = args["amount"]
    account["balance"] = _quantize(account["balance"] + amount)
    return OperationResult(
        summary=f"credited {amount} new_balance={account['balance']}",
        data={"amount": str(amount), "new_balance": str(account["balance"])},
    )


def check_apply_debit(account: AccountState, args: dict[str, Any]) -> None:
    amount = args.get("amount")
    if not isinstance(amount, Decimal):
        raise OperationError("apply_debit: amount is required")
    if amount < 0:
        raise OperationError("apply_debit: amount must not be negative")
    if account["frozen"]:
        raise OperationError("apply_debit: account is frozen")
    if account["balance"] < amount:
        raise OperationError("apply_debit: balance is below the amount")


def effect_apply_debit(account: AccountState, args: dict[str, Any]) -> OperationResult:
    amount: Decimal = args["amount"]
    account["balance"] = _quantize(account["balance"] - amount)
    return OperationResult(
        summary=f"debited {amount} new_balance={account['balance']}",
        data={"amount": str(amount), "new_balance": str(account["balance"])},
    )


def check_freeze_account(account: AccountState, args: dict[str, Any]) -> None:
    if account["frozen"]:
        raise OperationError("freeze_account: account is frozen")


def effect_freeze_account(account: AccountState, args: dict[str, Any]) -> OperationResult:
    account["frozen"] = True
    return OperationResult(summary="account frozen", data={"frozen": True})


def check_unfreeze_account(account: AccountState, args: dict[str, Any]) -> None:
    return None


def effect_unfreeze_account(account: AccountState, args: dict[str, Any]) -> OperationResult:
    account["frozen"] = False
    return OperationResult(summary="account unfrozen", data={"frozen": False})


def check_notify_customer(account: AccountState, args: dict[str, Any]) -> None:
    origin = args.get("origin")
    if origin not in NOTIFY_TEMPLATES:
        raise OperationError(f"notify_customer: origin {origin!r} is not supported")
    if account["frozen"]:
        raise OperationError("notify_customer: account is frozen")


def effect_notify_customer(account: AccountState, args: dict[str, Any]) -> OperationResult:
    origin = args["origin"]
    message = NOTIFY_TEMPLATES[origin]
    return OperationResult(summary=f"notified customer: {message}", data={"message": message})


@dataclass(frozen=True)
class Operation:
    name: str
    materiality: str  # "read" or "write"
    check: Callable[[AccountState, dict[str, Any]], None]
    effect: Callable[[AccountState, dict[str, Any]], OperationResult]


OPERATIONS: dict[str, Operation] = {
    "read_account": Operation("read_account", "read", check_read_account, effect_read_account),
    "apply_credit": Operation(
        "apply_credit", "write", check_apply_credit, effect_apply_credit
    ),
    "apply_debit": Operation("apply_debit", "write", check_apply_debit, effect_apply_debit),
    "freeze_account": Operation(
        "freeze_account", "write", check_freeze_account, effect_freeze_account
    ),
    "unfreeze_account": Operation(
        "unfreeze_account", "write", check_unfreeze_account, effect_unfreeze_account
    ),
    "notify_customer": Operation(
        "notify_customer", "write", check_notify_customer, effect_notify_customer
    ),
}


def run_operation(name: str, account: AccountState, args: dict[str, Any]) -> OperationResult:
    """Check then run the named operation, raising OperationError on failure."""

    operation = OPERATIONS[name]
    operation.check(account, args)
    return operation.effect(account, args)

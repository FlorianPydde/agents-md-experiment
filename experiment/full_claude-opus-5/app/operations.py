"""The six supported operations. One registry entry carries every behavior."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .domain import Account, OperationOutcome
from .enums import Materiality, OperationName, Origin
from .errors import AppError, OperationFailure
from .values import Money


@dataclass(frozen=True)
class OperationArgs:
    """The arguments a step hands to an operation.

    Operations differ in what they need, so fields an operation does not use are
    absent. Each operation's check turns absence into a clear failure.
    """

    account_id: str
    amount: Money | None = None
    origin: Origin | None = None

    def require_amount(self) -> Money:
        if self.amount is None:
            raise AppError("operation requires an amount")
        return self.amount

    def require_origin(self) -> Origin:
        if self.origin is None:
            raise AppError("operation requires a requester origin")
        return self.origin


CheckFn = Callable[[OperationArgs], None]
EffectFn = Callable[[Account, OperationArgs], OperationOutcome]


@dataclass(frozen=True)
class Operation:
    name: OperationName
    materiality: Materiality
    check: CheckFn
    effect: EffectFn

    def run(self, account: Account, args: OperationArgs) -> OperationOutcome:
        self.check(args)
        if self.materiality is Materiality.WRITE:
            account.ensure_writable(self.name)
        return self.effect(account, args)


def _check_nothing(args: OperationArgs) -> None:
    return None


def _check_amount(args: OperationArgs) -> None:
    args.require_amount()


def _check_origin(args: OperationArgs) -> None:
    args.require_origin()


def read_account(account: Account, args: OperationArgs) -> OperationOutcome:
    return OperationOutcome(account.describe(), account)


def apply_credit(account: Account, args: OperationArgs) -> OperationOutcome:
    amount = args.require_amount()
    updated = account.credited(amount)
    return OperationOutcome(f"credited {amount}, balance {updated.balance}", updated)


def apply_debit(account: Account, args: OperationArgs) -> OperationOutcome:
    amount = args.require_amount()
    updated = account.debited(amount)
    return OperationOutcome(f"debited {amount}, balance {updated.balance}", updated)


def freeze_account(account: Account, args: OperationArgs) -> OperationOutcome:
    updated = account.frozen_mark(True)
    return OperationOutcome("account frozen", updated)


def unfreeze_account(account: Account, args: OperationArgs) -> OperationOutcome:
    updated = account.frozen_mark(False)
    return OperationOutcome("account unfrozen", updated)


# Exactly two templates, chosen by origin, and not expected to change: rule 22.
def notify_customer(account: Account, args: OperationArgs) -> OperationOutcome:
    origin = args.require_origin()
    if origin is Origin.INTERNAL:
        message = f"Dear {account.owner}, our team has updated your account."
    else:
        message = f"Dear {account.owner}, your request has been processed."
    return OperationOutcome(message, account)


OPERATIONS: dict[OperationName, Operation] = {
    OperationName.READ_ACCOUNT: Operation(
        OperationName.READ_ACCOUNT, Materiality.READ, _check_nothing, read_account
    ),
    OperationName.APPLY_CREDIT: Operation(
        OperationName.APPLY_CREDIT, Materiality.WRITE, _check_amount, apply_credit
    ),
    OperationName.APPLY_DEBIT: Operation(
        OperationName.APPLY_DEBIT, Materiality.WRITE, _check_amount, apply_debit
    ),
    OperationName.FREEZE_ACCOUNT: Operation(
        OperationName.FREEZE_ACCOUNT, Materiality.WRITE, _check_nothing, freeze_account
    ),
    OperationName.UNFREEZE_ACCOUNT: Operation(
        OperationName.UNFREEZE_ACCOUNT,
        Materiality.WRITE,
        _check_nothing,
        unfreeze_account,
    ),
    OperationName.NOTIFY_CUSTOMER: Operation(
        OperationName.NOTIFY_CUSTOMER,
        Materiality.WRITE,
        _check_origin,
        notify_customer,
    ),
}

if _missing := set(OperationName) - OPERATIONS.keys():
    raise RuntimeError(f"No operation registered for: {_missing}")

__all__ = [
    "OPERATIONS",
    "Operation",
    "OperationArgs",
    "OperationFailure",
]

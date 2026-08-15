"""Operation registry: the checks and effects of the six supported operations.

Each entry pairs a check (raises DomainError when arguments are unacceptable
before running) with an effect (the change it makes when it runs), keyed by
an enum so a missing entry is caught at import time (rules 18, 20).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.domain import (
    Account,
    DomainError,
    Materiality,
    Money,
    OperationName,
    Origin,
    Requester,
)

_NOTIFICATION_TEMPLATES: dict[Origin, str] = {
    Origin.INTERNAL: "We have completed the requested action on your account.",
    Origin.EXTERNAL: "Thank you for contacting us. Your request has been handled.",
}

if missing := set(Origin) - _NOTIFICATION_TEMPLATES.keys():
    raise RuntimeError(f"No notification template registered for: {missing}")


@dataclass(frozen=True)
class OperationContext:
    account: Account
    amount: Money
    requester: Requester


@dataclass(frozen=True)
class OperationOutcome:
    account: Account
    detail: str


def _check_none(ctx: OperationContext) -> None:
    return None


def _check_write_not_frozen(ctx: OperationContext) -> None:
    if ctx.account.frozen:
        raise DomainError(f"account {ctx.account.id} is frozen")


def _execute_read_account(ctx: OperationContext) -> OperationOutcome:
    state = "frozen" if ctx.account.frozen else "active"
    detail = f"balance={ctx.account.balance.formatted()} tier={ctx.account.tier.value} state={state}"
    return OperationOutcome(account=ctx.account, detail=detail)


def _execute_apply_credit(ctx: OperationContext) -> OperationOutcome:
    account = ctx.account.credited(ctx.amount)
    detail = f"credited {ctx.amount.formatted()}, new balance {account.balance.formatted()}"
    return OperationOutcome(account=account, detail=detail)


def _execute_apply_debit(ctx: OperationContext) -> OperationOutcome:
    account = ctx.account.debited(ctx.amount)
    detail = f"debited {ctx.amount.formatted()}, new balance {account.balance.formatted()}"
    return OperationOutcome(account=account, detail=detail)


def _execute_freeze_account(ctx: OperationContext) -> OperationOutcome:
    account = ctx.account.frozen_copy()
    return OperationOutcome(account=account, detail=f"account {account.id} frozen")


def _execute_unfreeze_account(ctx: OperationContext) -> OperationOutcome:
    account = ctx.account.unfrozen_copy()
    return OperationOutcome(account=account, detail=f"account {account.id} unfrozen")


def _execute_notify_customer(ctx: OperationContext) -> OperationOutcome:
    message = _NOTIFICATION_TEMPLATES[ctx.requester.origin]
    return OperationOutcome(account=ctx.account, detail=message)


@dataclass(frozen=True)
class Operation:
    materiality: Materiality
    check: Callable[[OperationContext], None]
    execute: Callable[[OperationContext], OperationOutcome]


OPERATIONS: dict[OperationName, Operation] = {
    OperationName.READ_ACCOUNT: Operation(
        materiality=Materiality.READ,
        check=_check_none,
        execute=_execute_read_account,
    ),
    OperationName.APPLY_CREDIT: Operation(
        materiality=Materiality.WRITE,
        check=_check_write_not_frozen,
        execute=_execute_apply_credit,
    ),
    OperationName.APPLY_DEBIT: Operation(
        materiality=Materiality.WRITE,
        check=_check_write_not_frozen,
        execute=_execute_apply_debit,
    ),
    OperationName.FREEZE_ACCOUNT: Operation(
        materiality=Materiality.WRITE,
        check=_check_write_not_frozen,
        execute=_execute_freeze_account,
    ),
    OperationName.UNFREEZE_ACCOUNT: Operation(
        materiality=Materiality.WRITE,
        check=_check_none,
        execute=_execute_unfreeze_account,
    ),
    OperationName.NOTIFY_CUSTOMER: Operation(
        materiality=Materiality.WRITE,
        check=_check_write_not_frozen,
        execute=_execute_notify_customer,
    ),
}

if missing := set(OperationName) - OPERATIONS.keys():
    raise RuntimeError(f"No operation registered for: {missing}")


def operation_for(name: OperationName) -> Operation:
    return OPERATIONS[name]

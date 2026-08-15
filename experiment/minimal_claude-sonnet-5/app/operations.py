"""Supported operations.

Each operation has a materiality (already in `domain.PLANS`/`OPERATION_MATERIALITY`),
a check that its arguments are acceptable, and an effect. Arguments arrive as
free-form data assembled from the plan step and the request; operations are
looked up by `OperationName` in a registry rather than an if/elif chain.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.domain import Account, OperationName, Origin, ServiceRequest


class OperationError(Exception):
    """Raised when an operation's precondition check fails at run time."""


@dataclass(frozen=True)
class OperationArgs:
    """Free-form-ish arguments passed to an operation, drawn from the request."""

    account: Account
    amount: Decimal
    origin: Origin


@dataclass(frozen=True)
class OperationResult:
    account: Account
    """The (possibly updated) account after the operation ran."""

    detail: str
    """A short human-readable description of the effect, for the log."""


NOTIFY_TEMPLATES: dict[Origin, str] = {
    Origin.INTERNAL: "Internal notice sent to customer regarding their request.",
    Origin.EXTERNAL: "External notice sent to customer regarding their request.",
}


def _check_not_frozen(args: OperationArgs, op_name: str) -> None:
    if args.account.frozen:
        raise OperationError(f"{op_name} failed: account {args.account.id} is frozen")


def check_read_account(args: OperationArgs) -> None:
    return None


def run_read_account(args: OperationArgs) -> OperationResult:
    a = args.account
    return OperationResult(
        account=a,
        detail=f"balance={a.balance:.2f} tier={a.tier.value} frozen={a.frozen}",
    )


def check_apply_credit(args: OperationArgs) -> None:
    _check_not_frozen(args, "apply_credit")


def run_apply_credit(args: OperationArgs) -> OperationResult:
    a = args.account
    new_balance = a.balance + args.amount
    updated = replace(a, balance=new_balance)
    return OperationResult(account=updated, detail=f"credited {args.amount:.2f}, new balance {new_balance:.2f}")


def check_apply_debit(args: OperationArgs) -> None:
    _check_not_frozen(args, "apply_debit")
    if args.account.balance < args.amount:
        raise OperationError(
            f"apply_debit failed: balance {args.account.balance:.2f} is below amount {args.amount:.2f}"
        )


def run_apply_debit(args: OperationArgs) -> OperationResult:
    a = args.account
    new_balance = a.balance - args.amount
    updated = replace(a, balance=new_balance)
    return OperationResult(account=updated, detail=f"debited {args.amount:.2f}, new balance {new_balance:.2f}")


def check_freeze_account(args: OperationArgs) -> None:
    _check_not_frozen(args, "freeze_account")


def run_freeze_account(args: OperationArgs) -> OperationResult:
    updated = replace(args.account, frozen=True)
    return OperationResult(account=updated, detail="account frozen")


def check_unfreeze_account(args: OperationArgs) -> None:
    return None


def run_unfreeze_account(args: OperationArgs) -> OperationResult:
    updated = replace(args.account, frozen=False)
    return OperationResult(account=updated, detail="account unfrozen")


def check_notify_customer(args: OperationArgs) -> None:
    _check_not_frozen(args, "notify_customer")


def run_notify_customer(args: OperationArgs) -> OperationResult:
    message = NOTIFY_TEMPLATES[args.origin]
    return OperationResult(account=args.account, detail=message)


@dataclass(frozen=True)
class Operation:
    name: OperationName
    check: object
    run: object


OPERATIONS: dict[OperationName, Operation] = {
    OperationName.READ_ACCOUNT: Operation(OperationName.READ_ACCOUNT, check_read_account, run_read_account),
    OperationName.APPLY_CREDIT: Operation(OperationName.APPLY_CREDIT, check_apply_credit, run_apply_credit),
    OperationName.APPLY_DEBIT: Operation(OperationName.APPLY_DEBIT, check_apply_debit, run_apply_debit),
    OperationName.FREEZE_ACCOUNT: Operation(
        OperationName.FREEZE_ACCOUNT, check_freeze_account, run_freeze_account
    ),
    OperationName.UNFREEZE_ACCOUNT: Operation(
        OperationName.UNFREEZE_ACCOUNT, check_unfreeze_account, run_unfreeze_account
    ),
    OperationName.NOTIFY_CUSTOMER: Operation(
        OperationName.NOTIFY_CUSTOMER, check_notify_customer, run_notify_customer
    ),
}


def execute(name: OperationName, args: OperationArgs) -> OperationResult:
    """Check and run the named operation, raising OperationError on failure."""
    op = OPERATIONS[name]
    op.check(args)
    return op.run(args)

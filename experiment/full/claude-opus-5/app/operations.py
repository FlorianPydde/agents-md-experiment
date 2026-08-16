"""The six supported operations. One registry entry holds everything about a case."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domain import Account, Money, ServiceRequest
from app.enums import Materiality, OperationName, Origin


@dataclass(frozen=True)
class AccountArgs:
    """Arguments of an operation that only names an account."""

    account: str


@dataclass(frozen=True)
class AmountArgs:
    """Arguments of an operation that moves money."""

    account: str
    amount: Money


@dataclass(frozen=True)
class NotifyArgs:
    """Arguments of the customer notification."""

    account: str
    origin: Origin


type StepArgs = AccountArgs | AmountArgs | NotifyArgs


@dataclass(frozen=True)
class OperationOutcome:
    """The account after the operation, and what to record about the run."""

    account: Account
    detail: str


@dataclass(frozen=True)
class Operation[A]:
    """Everything one operation is: how material it is, how it is called, and what it does."""

    materiality: Materiality
    build_args: Callable[[ServiceRequest], A]
    check: Callable[[Account, A], None]
    apply: Callable[[Account, A], OperationOutcome]

    def run(self, account: Account, args: A) -> OperationOutcome:
        self.check(account, args)
        return self.apply(account, args)


NOTIFICATIONS: dict[Origin, str] = {
    Origin.INTERNAL: "internal note filed for {owner} on account {account}",
    Origin.EXTERNAL: "letter sent to {owner} for account {account}",
}


def account_args(request: ServiceRequest) -> AccountArgs:
    return AccountArgs(account=request.account)


def amount_args(request: ServiceRequest) -> AmountArgs:
    return AmountArgs(account=request.account, amount=request.amount)


def notify_args(request: ServiceRequest) -> NotifyArgs:
    return NotifyArgs(account=request.account, origin=request.requester.origin)


def check_always_allowed(account: Account, args: StepArgs) -> None:
    """A read, and the unfreeze that clears the frozen mark, are always acceptable."""


def check_account_active(account: Account, args: StepArgs) -> None:
    account.ensure_active()


def check_debit(account: Account, args: AmountArgs) -> None:
    account.ensure_active()
    account.ensure_covers(args.amount)


def apply_read(account: Account, args: AccountArgs) -> OperationOutcome:
    return OperationOutcome(
        account=account,
        detail=(
            f"balance {account.balance}, tier {account.tier}, "
            f"{account.state_word()}"
        ),
    )


def apply_credit(account: Account, args: AmountArgs) -> OperationOutcome:
    credited = account.credited(args.amount)
    return OperationOutcome(
        account=credited, detail=f"credited {args.amount}, balance {credited.balance}"
    )


def apply_debit(account: Account, args: AmountArgs) -> OperationOutcome:
    debited = account.debited(args.amount)
    return OperationOutcome(
        account=debited, detail=f"debited {args.amount}, balance {debited.balance}"
    )


def apply_freeze(account: Account, args: AccountArgs) -> OperationOutcome:
    return OperationOutcome(account=account.with_frozen(True), detail="account frozen")


def apply_unfreeze(account: Account, args: AccountArgs) -> OperationOutcome:
    return OperationOutcome(
        account=account.with_frozen(False), detail="account unfrozen"
    )


def apply_notify(account: Account, args: NotifyArgs) -> OperationOutcome:
    message = NOTIFICATIONS[args.origin].format(
        owner=account.owner, account=account.id
    )
    return OperationOutcome(account=account, detail=message)


OPERATIONS: dict[OperationName, Operation[Any]] = {
    OperationName.READ_ACCOUNT: Operation(
        materiality=Materiality.READ,
        build_args=account_args,
        check=check_always_allowed,
        apply=apply_read,
    ),
    OperationName.APPLY_CREDIT: Operation(
        materiality=Materiality.WRITE,
        build_args=amount_args,
        check=check_account_active,
        apply=apply_credit,
    ),
    OperationName.APPLY_DEBIT: Operation(
        materiality=Materiality.WRITE,
        build_args=amount_args,
        check=check_debit,
        apply=apply_debit,
    ),
    OperationName.FREEZE_ACCOUNT: Operation(
        materiality=Materiality.WRITE,
        build_args=account_args,
        check=check_account_active,
        apply=apply_freeze,
    ),
    OperationName.UNFREEZE_ACCOUNT: Operation(
        materiality=Materiality.WRITE,
        build_args=account_args,
        check=check_always_allowed,
        apply=apply_unfreeze,
    ),
    OperationName.NOTIFY_CUSTOMER: Operation(
        materiality=Materiality.WRITE,
        build_args=notify_args,
        check=check_account_active,
        apply=apply_notify,
    ),
}

if missing := set(OperationName) - OPERATIONS.keys():
    raise RuntimeError(f"No operation registered for: {missing}")

if absent := set(Origin) - NOTIFICATIONS.keys():
    raise RuntimeError(f"No notification template registered for: {absent}")

"""The six supported operations: materiality, argument check, and effect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from .domain import Materiality, OperationCall, OperationName, Origin
from .errors import AppError
from .world import World


class OperationFailure(Exception):
    """An operation refused to run or could not complete. Fails its request."""


@dataclass(frozen=True)
class OperationOutcome:
    """What an operation reports after running. Ordered detail for the log."""

    detail: tuple[tuple[str, str], ...]


CheckFn = Callable[[OperationCall, World], None]
EffectFn = Callable[[OperationCall, World], OperationOutcome]


@dataclass(frozen=True)
class Operation:
    name: OperationName
    materiality: Materiality
    check: CheckFn
    effect: EffectFn


def _require_known_account(call: OperationCall, world: World) -> None:
    try:
        world.get(call.account)
    except AppError as error:
        raise OperationFailure(str(error)) from None


def _require_thawed(call: OperationCall, world: World) -> None:
    """Every write other than unfreeze_account refuses a frozen account."""
    if world.get(call.account).frozen:
        raise OperationFailure(f"account '{call.account}' is frozen")


def _require_positive_amount(call: OperationCall) -> None:
    if call.amount <= Decimal("0"):
        raise OperationFailure(f"amount must be greater than zero, got {call.amount}")


def _check_read(call: OperationCall, world: World) -> None:
    _require_known_account(call, world)


def _check_credit(call: OperationCall, world: World) -> None:
    _require_known_account(call, world)
    _require_positive_amount(call)
    _require_thawed(call, world)


def _check_debit(call: OperationCall, world: World) -> None:
    _require_known_account(call, world)
    _require_positive_amount(call)
    _require_thawed(call, world)
    account = world.get(call.account)
    if account.balance < call.amount:
        raise OperationFailure(
            f"balance {account.balance} is below the amount {call.amount}"
        )


def _check_freeze(call: OperationCall, world: World) -> None:
    _require_known_account(call, world)
    _require_thawed(call, world)


def _check_unfreeze(call: OperationCall, world: World) -> None:
    _require_known_account(call, world)


def _check_notify(call: OperationCall, world: World) -> None:
    _require_known_account(call, world)
    _require_thawed(call, world)


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _effect_read(call: OperationCall, world: World) -> OperationOutcome:
    account = world.get(call.account)
    return OperationOutcome(
        (
            ("account", account.id),
            ("balance", _money(account.balance)),
            ("tier", account.tier.value),
            ("frozen", "true" if account.frozen else "false"),
        )
    )


def _effect_credit(call: OperationCall, world: World) -> OperationOutcome:
    account = world.credit(call.account, call.amount)
    return OperationOutcome(
        (
            ("account", account.id),
            ("credited", _money(call.amount)),
            ("balance", _money(account.balance)),
        )
    )


def _effect_debit(call: OperationCall, world: World) -> OperationOutcome:
    account = world.debit(call.account, call.amount)
    return OperationOutcome(
        (
            ("account", account.id),
            ("debited", _money(call.amount)),
            ("balance", _money(account.balance)),
        )
    )


def _effect_freeze(call: OperationCall, world: World) -> OperationOutcome:
    account = world.set_frozen(call.account, True)
    return OperationOutcome((("account", account.id), ("frozen", "true")))


def _effect_unfreeze(call: OperationCall, world: World) -> OperationOutcome:
    account = world.set_frozen(call.account, False)
    return OperationOutcome((("account", account.id), ("frozen", "false")))


INTERNAL_TEMPLATE = "Internal note: request on account {account} has been actioned."
EXTERNAL_TEMPLATE = "Dear customer, your request on account {account} has been actioned."


def message_for(origin: Origin, account_id: str) -> str:
    template = INTERNAL_TEMPLATE if origin is Origin.INTERNAL else EXTERNAL_TEMPLATE
    return template.format(account=account_id)


def _effect_notify(call: OperationCall, world: World) -> OperationOutcome:
    account = world.get(call.account)
    return OperationOutcome(
        (
            ("account", account.id),
            ("origin", call.origin.value),
            ("message", message_for(call.origin, account.id)),
        )
    )


OPERATIONS: dict[OperationName, Operation] = {
    OperationName.READ_ACCOUNT: Operation(
        OperationName.READ_ACCOUNT, Materiality.READ, _check_read, _effect_read
    ),
    OperationName.APPLY_CREDIT: Operation(
        OperationName.APPLY_CREDIT, Materiality.WRITE, _check_credit, _effect_credit
    ),
    OperationName.APPLY_DEBIT: Operation(
        OperationName.APPLY_DEBIT, Materiality.WRITE, _check_debit, _effect_debit
    ),
    OperationName.FREEZE_ACCOUNT: Operation(
        OperationName.FREEZE_ACCOUNT, Materiality.WRITE, _check_freeze, _effect_freeze
    ),
    OperationName.UNFREEZE_ACCOUNT: Operation(
        OperationName.UNFREEZE_ACCOUNT, Materiality.WRITE, _check_unfreeze, _effect_unfreeze
    ),
    OperationName.NOTIFY_CUSTOMER: Operation(
        OperationName.NOTIFY_CUSTOMER, Materiality.WRITE, _check_notify, _effect_notify
    ),
}


def run_operation(call: OperationCall, world: World) -> OperationOutcome:
    operation = OPERATIONS[call.operation]
    operation.check(call, world)
    return operation.effect(call, world)

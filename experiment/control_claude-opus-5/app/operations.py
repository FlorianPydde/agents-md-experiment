"""The six supported operations.

Each operation declares its materiality, validates its own free form arguments
before it runs, and returns ordered detail about what it did. Operations are
registered by name so new ones can be added without touching the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping

from .errors import InputError, OperationError
from .models import Materiality, Origin, parse_amount
from .world import World

# The two fixed notification templates. Chosen by requester origin; these two
# are the only ones and are not expected to change.
NOTIFICATION_TEMPLATES: Mapping[Origin, str] = {
    Origin.INTERNAL: "Internal notice: request {reference} on account {account} has been processed.",
    Origin.EXTERNAL: "Dear {owner}, your request {reference} regarding account {account} has been processed.",
}


@dataclass(frozen=True)
class Operation:
    name: str
    materiality: Materiality
    description: str
    _validate: Callable[[Mapping[str, Any]], None]
    _run: Callable[[World, Mapping[str, Any]], dict[str, Any]]

    def validate(self, args: Mapping[str, Any]) -> None:
        if "account" not in args or not isinstance(args["account"], str):
            raise InputError(f"operation {self.name!r} requires a string 'account' argument")
        self._validate(args)

    def run(self, world: World, args: Mapping[str, Any]) -> dict[str, Any]:
        self.validate(args)
        account = world.account(str(args["account"]))
        if self.materiality is Materiality.WRITE and self.name != "unfreeze_account" and account.frozen:
            raise OperationError(f"account {account.id} is frozen; {self.name} cannot run")
        return self._run(world, args)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "materiality": self.materiality.value,
            "description": self.description,
        }


def _needs_amount(args: Mapping[str, Any]) -> Decimal:
    if "amount" not in args:
        raise InputError("operation requires an 'amount' argument")
    return parse_amount(args["amount"], "amount")


def _no_extra_validation(args: Mapping[str, Any]) -> None:
    return None


def _validate_amount(args: Mapping[str, Any]) -> None:
    _needs_amount(args)


def _validate_notify(args: Mapping[str, Any]) -> None:
    Origin.parse(args.get("origin"), "origin")
    if not isinstance(args.get("reference"), str):
        raise InputError("operation 'notify_customer' requires a string 'reference' argument")


def _read_account(world: World, args: Mapping[str, Any]) -> dict[str, Any]:
    account = world.account(str(args["account"]))
    return {
        "account": account.id,
        "balance": format(account.balance, ".2f"),
        "tier": account.tier.value,
        "frozen": account.frozen,
    }


def _apply_credit(world: World, args: Mapping[str, Any]) -> dict[str, Any]:
    amount = _needs_amount(args)
    balance = world.credit(str(args["account"]), amount)
    return {
        "account": str(args["account"]),
        "amount": format(amount, ".2f"),
        "balance": format(balance, ".2f"),
    }


def _apply_debit(world: World, args: Mapping[str, Any]) -> dict[str, Any]:
    amount = _needs_amount(args)
    account = world.account(str(args["account"]))
    if account.balance < amount:
        raise OperationError(
            f"account {account.id} balance {format(account.balance, '.2f')} "
            f"is below the debit amount {format(amount, '.2f')}"
        )
    balance = world.debit(account.id, amount)
    return {
        "account": account.id,
        "amount": format(amount, ".2f"),
        "balance": format(balance, ".2f"),
    }


def _freeze_account(world: World, args: Mapping[str, Any]) -> dict[str, Any]:
    world.set_frozen(str(args["account"]), True)
    return {"account": str(args["account"]), "frozen": True}


def _unfreeze_account(world: World, args: Mapping[str, Any]) -> dict[str, Any]:
    world.set_frozen(str(args["account"]), False)
    return {"account": str(args["account"]), "frozen": False}


def _notify_customer(world: World, args: Mapping[str, Any]) -> dict[str, Any]:
    origin = Origin.parse(args.get("origin"), "origin")
    account = world.account(str(args["account"]))
    message = NOTIFICATION_TEMPLATES[origin].format(
        reference=args["reference"], account=account.id, owner=account.owner
    )
    return {"account": account.id, "origin": origin.value, "message": message}


REGISTRY: dict[str, Operation] = {
    operation.name: operation
    for operation in (
        Operation(
            "read_account",
            Materiality.READ,
            "Reports balance, tier and frozen state. Changes nothing.",
            _no_extra_validation,
            _read_account,
        ),
        Operation(
            "apply_credit",
            Materiality.WRITE,
            "Increases the balance by the amount.",
            _validate_amount,
            _apply_credit,
        ),
        Operation(
            "apply_debit",
            Materiality.WRITE,
            "Decreases the balance by the amount. Fails when the balance is below the amount.",
            _validate_amount,
            _apply_debit,
        ),
        Operation(
            "freeze_account",
            Materiality.WRITE,
            "Marks the account frozen.",
            _no_extra_validation,
            _freeze_account,
        ),
        Operation(
            "unfreeze_account",
            Materiality.WRITE,
            "Clears the frozen mark.",
            _no_extra_validation,
            _unfreeze_account,
        ),
        Operation(
            "notify_customer",
            Materiality.WRITE,
            "Records that the customer was told. Changes no balance.",
            _validate_notify,
            _notify_customer,
        ),
    )
}


def get(name: str) -> Operation:
    try:
        return REGISTRY[name]
    except KeyError:
        raise InputError(f"unknown operation {name!r}") from None

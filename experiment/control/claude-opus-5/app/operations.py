"""The six supported operations.

Each operation declares its materiality, checks that the free form arguments it
was handed are acceptable, and then applies its effect.  Operations are held in
a registry so that adding a seventh is a matter of writing one class and
decorating it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

from .errors import AppError, OperationFailure
from .loader import parse_amount
from .models import Materiality, Origin
from .world import World

#: The only two messages `notify_customer` may send, chosen by requester origin.
MESSAGE_TEMPLATES: dict[Origin, str] = {
    Origin.INTERNAL: "Request {reference} on account {account} was handled by our team.",
    Origin.EXTERNAL: "Dear customer, your request {reference} on account {account} was handled.",
}


class Operation:
    """Base class for an operation."""

    name: ClassVar[str]
    materiality: ClassVar[Materiality]
    description: ClassVar[str]

    def check(self, world: World, arguments: dict[str, Any]) -> None:
        """Reject arguments that this operation cannot work with."""

    def effect(self, world: World, arguments: dict[str, Any]) -> dict[str, Any]:
        """Apply the operation and report ordered data about what it did."""
        raise NotImplementedError

    def run(self, world: World, arguments: dict[str, Any]) -> dict[str, Any]:
        """Check the arguments, guard frozen accounts, then apply the effect."""
        account_id = _text(arguments, "account", self.name)
        if not world.has(account_id):
            raise AppError(f"operation '{self.name}' names unknown account '{account_id}'")
        self.check(world, arguments)
        if self.materiality is Materiality.WRITE and self.name != "unfreeze_account":
            if world.account(account_id).frozen:
                raise OperationFailure(f"account {account_id} is frozen")
        return self.effect(world, arguments)


REGISTRY: dict[str, Operation] = {}


def operation(cls: type[Operation]) -> type[Operation]:
    REGISTRY[cls.name] = cls()
    return cls


def get(name: str) -> Operation:
    try:
        return REGISTRY[name]
    except KeyError:
        raise AppError(f"unknown operation '{name}'") from None


def catalogue() -> list[dict[str, str]]:
    """The supported operations, for reports and the HTTP API."""
    return [
        {
            "operation": item.name,
            "materiality": str(item.materiality),
            "description": item.description,
        }
        for item in (REGISTRY[name] for name in sorted(REGISTRY))
    ]


def _text(arguments: dict[str, Any], field: str, name: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AppError(f"operation '{name}' needs a '{field}' argument")
    return value


def _amount(arguments: dict[str, Any], name: str) -> Decimal:
    if "amount" not in arguments or arguments["amount"] is None:
        raise AppError(f"operation '{name}' needs an 'amount' argument")
    value = arguments["amount"]
    if isinstance(value, Decimal):
        if value < 0:
            raise AppError(f"operation '{name}' was given a negative amount")
        return value
    return parse_amount(value, f"operation '{name}' argument 'amount'")


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


@operation
class ReadAccount(Operation):
    name = "read_account"
    materiality = Materiality.READ
    description = "Reports balance, tier and frozen state. Changes nothing."

    def effect(self, world: World, arguments: dict[str, Any]) -> dict[str, Any]:
        account = world.account(arguments["account"])
        return {
            "account": account.id,
            "owner": account.owner,
            "tier": str(account.tier),
            "balance": _money(account.balance),
            "frozen": account.frozen,
        }


@operation
class ApplyCredit(Operation):
    name = "apply_credit"
    materiality = Materiality.WRITE
    description = "Increases the balance by the amount."

    def check(self, world: World, arguments: dict[str, Any]) -> None:
        _amount(arguments, self.name)

    def effect(self, world: World, arguments: dict[str, Any]) -> dict[str, Any]:
        amount = _amount(arguments, self.name)
        account_id = arguments["account"]
        before = world.account(account_id).balance
        after = world.credit(account_id, amount)
        return {
            "account": account_id,
            "amount": _money(amount),
            "balance_before": _money(before),
            "balance_after": _money(after),
        }


@operation
class ApplyDebit(Operation):
    name = "apply_debit"
    materiality = Materiality.WRITE
    description = "Decreases the balance by the amount. Fails when the balance is below it."

    def check(self, world: World, arguments: dict[str, Any]) -> None:
        _amount(arguments, self.name)

    def effect(self, world: World, arguments: dict[str, Any]) -> dict[str, Any]:
        amount = _amount(arguments, self.name)
        account_id = arguments["account"]
        before = world.account(account_id).balance
        if before < amount:
            raise OperationFailure(
                f"account {account_id} holds {_money(before)} "
                f"which is below the requested {_money(amount)}"
            )
        after = world.debit(account_id, amount)
        return {
            "account": account_id,
            "amount": _money(amount),
            "balance_before": _money(before),
            "balance_after": _money(after),
        }


@operation
class FreezeAccount(Operation):
    name = "freeze_account"
    materiality = Materiality.WRITE
    description = "Marks the account frozen."

    def effect(self, world: World, arguments: dict[str, Any]) -> dict[str, Any]:
        account_id = arguments["account"]
        world.set_frozen(account_id, True)
        return {"account": account_id, "frozen": True}


@operation
class UnfreezeAccount(Operation):
    name = "unfreeze_account"
    materiality = Materiality.WRITE
    description = "Clears the frozen mark."

    def effect(self, world: World, arguments: dict[str, Any]) -> dict[str, Any]:
        account_id = arguments["account"]
        was_frozen = world.account(account_id).frozen
        world.set_frozen(account_id, False)
        return {"account": account_id, "was_frozen": was_frozen, "frozen": False}


@operation
class NotifyCustomer(Operation):
    name = "notify_customer"
    materiality = Materiality.WRITE
    description = "Records that the customer was told. Changes no balance."

    def check(self, world: World, arguments: dict[str, Any]) -> None:
        _text(arguments, "reference", self.name)
        origin = arguments.get("origin")
        try:
            Origin(str(origin))
        except ValueError:
            raise AppError(
                f"operation '{self.name}' needs an 'origin' of internal or external"
            ) from None

    def effect(self, world: World, arguments: dict[str, Any]) -> dict[str, Any]:
        origin = Origin(str(arguments["origin"]))
        account_id = arguments["account"]
        message = MESSAGE_TEMPLATES[origin].format(
            reference=arguments["reference"], account=account_id
        )
        return {"account": account_id, "origin": str(origin), "message": message}

"""The six supported operations: their check and their effect.

Each operation receives the account row (a mutable dict with ``id``,
``owner``, ``tier``, ``balance`` (``Decimal``) and ``frozen``) plus the
request the step belongs to, checks that its arguments are acceptable, and
then has its effect. Operations that fail raise ``OperationFailed`` with a
human readable reason; the engine turns that into a failed request.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import format_amount

NOTIFY_TEMPLATES = {
    "internal": "Internal notice: request {reference} for account {account} has been handled.",
    "external": "Dear {owner}, your request {reference} has been handled.",
}


class OperationFailed(Exception):
    """Raised when an operation's check or effect fails."""


def _require_not_frozen(account: dict, operation: str) -> None:
    if operation != "unfreeze_account" and account["frozen"]:
        raise OperationFailed(f"account {account['id']} is frozen")


def read_account(account: dict, request: dict) -> dict:
    return {
        "account": account["id"],
        "balance": format_amount(account["balance"]),
        "tier": account["tier"],
        "frozen": account["frozen"],
    }


def apply_credit(account: dict, request: dict) -> dict:
    _require_not_frozen(account, "apply_credit")
    amount: Decimal = request["amount"]
    account["balance"] = account["balance"] + amount
    return {
        "account": account["id"],
        "amount": format_amount(amount),
        "balance": format_amount(account["balance"]),
    }


def apply_debit(account: dict, request: dict) -> dict:
    _require_not_frozen(account, "apply_debit")
    amount: Decimal = request["amount"]
    if account["balance"] < amount:
        raise OperationFailed(
            f"account {account['id']} balance"
            f" {format_amount(account['balance'])} is below amount {format_amount(amount)}"
        )
    account["balance"] = account["balance"] - amount
    return {
        "account": account["id"],
        "amount": format_amount(amount),
        "balance": format_amount(account["balance"]),
    }


def freeze_account(account: dict, request: dict) -> dict:
    _require_not_frozen(account, "freeze_account")
    account["frozen"] = True
    return {"account": account["id"], "frozen": True}


def unfreeze_account(account: dict, request: dict) -> dict:
    account["frozen"] = False
    return {"account": account["id"], "frozen": False}


def notify_customer(account: dict, request: dict) -> dict:
    _require_not_frozen(account, "notify_customer")
    origin = request["requester_origin"]
    template = NOTIFY_TEMPLATES[origin]
    message = template.format(
        reference=request["reference"],
        account=account["id"],
        owner=account["owner"],
    )
    return {"account": account["id"], "origin": origin, "message": message}


DISPATCH = {
    "read_account": read_account,
    "apply_credit": apply_credit,
    "apply_debit": apply_debit,
    "freeze_account": freeze_account,
    "unfreeze_account": unfreeze_account,
    "notify_customer": notify_customer,
}


def run_operation(operation: str, account: dict, request: dict) -> dict:
    """Run ``operation`` against ``account`` for ``request``.

    Raises ``OperationFailed`` when the operation's check or effect fails.
    """

    return DISPATCH[operation](account, request)

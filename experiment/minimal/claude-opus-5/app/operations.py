"""The six supported operations: their materiality, argument check, and effect."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from .domain import Account, Materiality, OperationName, Origin, Tier, parse_member
from .errors import InputError
from .money import format_amount, parse_amount

Details = tuple[tuple[str, str], ...]


class OperationFailure(Exception):
    """The operation was allowed to run but could not complete."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Ledger:
    """The accounts the system acts on, held by id."""

    def __init__(self, accounts: Mapping[str, Account]) -> None:
        self._accounts = dict(accounts)

    @classmethod
    def from_accounts(cls, accounts: tuple[Account, ...]) -> Ledger:
        return cls({account.id: account for account in accounts})

    def get(self, account_id: str) -> Account:
        account = self._accounts.get(account_id)
        if account is None:
            raise OperationFailure(f"unknown account {account_id}")
        return account

    def put(self, account: Account) -> None:
        self._accounts[account.id] = account

    def sorted_accounts(self) -> tuple[Account, ...]:
        return tuple(sorted(self._accounts.values(), key=lambda account: account.id))


@dataclass(frozen=True, slots=True)
class AccountArguments:
    account_id: str


@dataclass(frozen=True, slots=True)
class AmountArguments:
    account_id: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class NotifyArguments:
    account_id: str
    reference: str
    origin: Origin


MESSAGE_TEMPLATES: dict[Origin, str] = {
    Origin.INTERNAL: "Internal note for {owner}: request {reference} was handled on account {account}.",
    Origin.EXTERNAL: "Dear {owner}, your request {reference} on account {account} has been handled.",
}


def _required(arguments: Mapping[str, str], key: str, operation: OperationName) -> str:
    value = arguments.get(key)
    if value is None:
        raise InputError(f"{operation.value} requires the argument {key!r}")
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{operation.value} requires a non empty {key!r}")
    return value


class Operation[A](ABC):
    """One supported operation. Arguments are checked before the effect runs."""

    name: OperationName
    materiality: Materiality
    description: str

    @abstractmethod
    def prepare(self, arguments: Mapping[str, str]) -> A:
        """Check the free form arguments and return typed ones."""

    @abstractmethod
    def run(self, prepared: A, ledger: Ledger) -> Details:
        """Apply the effect and report ordered data about what happened."""

    def guard_frozen(self, account: Account) -> None:
        if account.frozen and self.name is not OperationName.UNFREEZE_ACCOUNT:
            raise OperationFailure(f"account {account.id} is frozen")


class _AccountOperation(Operation[AccountArguments]):
    def prepare(self, arguments: Mapping[str, str]) -> AccountArguments:
        return AccountArguments(_required(arguments, "account", self.name))


class _AmountOperation(Operation[AmountArguments]):
    def prepare(self, arguments: Mapping[str, str]) -> AmountArguments:
        account_id = _required(arguments, "account", self.name)
        amount = parse_amount(
            _required(arguments, "amount", self.name),
            field=f"{self.name.value} amount",
        )
        return AmountArguments(account_id, amount)


class ReadAccount(_AccountOperation):
    name = OperationName.READ_ACCOUNT
    materiality = Materiality.READ
    description = "Reports balance, tier and frozen state. Changes nothing."

    def run(self, prepared: AccountArguments, ledger: Ledger) -> Details:
        account = ledger.get(prepared.account_id)
        return (
            ("account", account.id),
            ("balance", format_amount(account.balance)),
            ("tier", Tier(account.tier).value),
            ("frozen", "yes" if account.frozen else "no"),
        )


class ApplyCredit(_AmountOperation):
    name = OperationName.APPLY_CREDIT
    materiality = Materiality.WRITE
    description = "Increases the balance by the amount."

    def run(self, prepared: AmountArguments, ledger: Ledger) -> Details:
        account = ledger.get(prepared.account_id)
        self.guard_frozen(account)
        updated = account.with_balance(account.balance + prepared.amount)
        ledger.put(updated)
        return (
            ("account", account.id),
            ("amount", format_amount(prepared.amount)),
            ("balance_before", format_amount(account.balance)),
            ("balance_after", format_amount(updated.balance)),
        )


class ApplyDebit(_AmountOperation):
    name = OperationName.APPLY_DEBIT
    materiality = Materiality.WRITE
    description = "Decreases the balance by the amount. Fails when the balance is below the amount."

    def run(self, prepared: AmountArguments, ledger: Ledger) -> Details:
        account = ledger.get(prepared.account_id)
        self.guard_frozen(account)
        if account.balance < prepared.amount:
            raise OperationFailure(
                f"balance {format_amount(account.balance)} is below "
                f"{format_amount(prepared.amount)} on account {account.id}"
            )
        updated = account.with_balance(account.balance - prepared.amount)
        ledger.put(updated)
        return (
            ("account", account.id),
            ("amount", format_amount(prepared.amount)),
            ("balance_before", format_amount(account.balance)),
            ("balance_after", format_amount(updated.balance)),
        )


class FreezeAccount(_AccountOperation):
    name = OperationName.FREEZE_ACCOUNT
    materiality = Materiality.WRITE
    description = "Marks the account frozen."

    def run(self, prepared: AccountArguments, ledger: Ledger) -> Details:
        account = ledger.get(prepared.account_id)
        self.guard_frozen(account)
        ledger.put(account.with_frozen(True))
        return (("account", account.id), ("frozen", "yes"))


class UnfreezeAccount(_AccountOperation):
    name = OperationName.UNFREEZE_ACCOUNT
    materiality = Materiality.WRITE
    description = "Clears the frozen mark."

    def run(self, prepared: AccountArguments, ledger: Ledger) -> Details:
        account = ledger.get(prepared.account_id)
        ledger.put(account.with_frozen(False))
        return (("account", account.id), ("frozen", "no"))


class NotifyCustomer(Operation[NotifyArguments]):
    name = OperationName.NOTIFY_CUSTOMER
    materiality = Materiality.WRITE
    description = "Records that the customer was told. Changes no balance."

    def prepare(self, arguments: Mapping[str, str]) -> NotifyArguments:
        return NotifyArguments(
            account_id=_required(arguments, "account", self.name),
            reference=_required(arguments, "reference", self.name),
            origin=parse_member(
                Origin,
                _required(arguments, "origin", self.name),
                field=f"{self.name.value} origin",
            ),
        )

    def run(self, prepared: NotifyArguments, ledger: Ledger) -> Details:
        account = ledger.get(prepared.account_id)
        self.guard_frozen(account)
        message = MESSAGE_TEMPLATES[prepared.origin].format(
            owner=account.owner, reference=prepared.reference, account=account.id
        )
        return (
            ("account", account.id),
            ("origin", prepared.origin.value),
            ("message", message),
        )


OPERATIONS: dict[OperationName, Operation] = {
    operation.name: operation
    for operation in (
        ReadAccount(),
        ApplyCredit(),
        ApplyDebit(),
        FreezeAccount(),
        UnfreezeAccount(),
        NotifyCustomer(),
    )
}


def operation_for(name: OperationName) -> Operation:
    return OPERATIONS[name]

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain import Account, OperationName, Origin, Tier
from app.errors import InputError
from app.money import format_amount, parse_amount
from app.operations import Ledger, OperationFailure, operation_for

OPEN = Account("ACC-1", "Ada", Tier.STANDARD, Decimal("100.00"), False)
FROZEN = Account("ACC-2", "Grace", Tier.PREMIUM, Decimal("100.00"), True)


@pytest.fixture
def ledger() -> Ledger:
    return Ledger.from_accounts((OPEN, FROZEN))


def run(name: OperationName, arguments: dict[str, str], ledger: Ledger):
    operation = operation_for(name)
    return operation.run(operation.prepare(arguments), ledger)


def test_read_changes_nothing(ledger: Ledger) -> None:
    details = run(OperationName.READ_ACCOUNT, {"account": "ACC-1"}, ledger)

    assert dict(details)["balance"] == "100.00"
    assert ledger.get("ACC-1") == OPEN


def test_credit_increases_the_balance(ledger: Ledger) -> None:
    run(OperationName.APPLY_CREDIT, {"account": "ACC-1", "amount": "25.50"}, ledger)

    assert ledger.get("ACC-1").balance == Decimal("125.50")


def test_debit_below_the_balance_fails(ledger: Ledger) -> None:
    with pytest.raises(OperationFailure, match="below"):
        run(OperationName.APPLY_DEBIT, {"account": "ACC-1", "amount": "100.01"}, ledger)

    assert ledger.get("ACC-1").balance == Decimal("100.00")


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        (OperationName.APPLY_CREDIT, {"account": "ACC-2", "amount": "1.00"}),
        (OperationName.APPLY_DEBIT, {"account": "ACC-2", "amount": "1.00"}),
        (OperationName.FREEZE_ACCOUNT, {"account": "ACC-2"}),
        (
            OperationName.NOTIFY_CUSTOMER,
            {"account": "ACC-2", "reference": "REQ-1", "origin": "external"},
        ),
    ],
)
def test_writes_fail_on_a_frozen_account(
    name: OperationName, arguments: dict[str, str], ledger: Ledger
) -> None:
    with pytest.raises(OperationFailure, match="frozen"):
        run(name, arguments, ledger)


def test_unfreeze_is_the_one_write_allowed_on_a_frozen_account(ledger: Ledger) -> None:
    run(OperationName.UNFREEZE_ACCOUNT, {"account": "ACC-2"}, ledger)

    assert ledger.get("ACC-2").frozen is False


def test_an_unknown_account_fails_the_operation(ledger: Ledger) -> None:
    with pytest.raises(OperationFailure, match="unknown account"):
        run(OperationName.READ_ACCOUNT, {"account": "ACC-9"}, ledger)


def test_notify_uses_the_template_for_the_origin(ledger: Ledger) -> None:
    internal = dict(
        run(
            OperationName.NOTIFY_CUSTOMER,
            {"account": "ACC-1", "reference": "REQ-1", "origin": Origin.INTERNAL.value},
            ledger,
        )
    )
    external = dict(
        run(
            OperationName.NOTIFY_CUSTOMER,
            {"account": "ACC-1", "reference": "REQ-1", "origin": Origin.EXTERNAL.value},
            ledger,
        )
    )

    assert internal["message"].startswith("Internal note")
    assert external["message"].startswith("Dear Ada")


def test_missing_arguments_are_reported(ledger: Ledger) -> None:
    with pytest.raises(InputError, match="requires the argument 'amount'"):
        run(OperationName.APPLY_CREDIT, {"account": "ACC-1"}, ledger)


def test_an_unknown_origin_is_reported(ledger: Ledger) -> None:
    with pytest.raises(InputError, match="must be one of"):
        run(
            OperationName.NOTIFY_CUSTOMER,
            {"account": "ACC-1", "reference": "REQ-1", "origin": "postal"},
            ledger,
        )


def test_negative_amounts_are_refused() -> None:
    with pytest.raises(InputError, match="must not be negative"):
        parse_amount("-1.00", field="amount")


def test_amounts_keep_two_decimals() -> None:
    assert format_amount(parse_amount("3", field="amount")) == "3.00"

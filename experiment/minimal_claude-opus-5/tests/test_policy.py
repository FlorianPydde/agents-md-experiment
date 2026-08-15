from decimal import Decimal

import pytest

from app.domain import OperationCall, OperationName, Origin, Role
from app.policy import evaluate


def call(operation: OperationName, amount: str = "10.00") -> OperationCall:
    return OperationCall(
        operation=operation,
        account="ACC-100",
        amount=Decimal(amount),
        origin=Origin.INTERNAL,
    )


def test_read_runs_on_its_own() -> None:
    decision = evaluate(call(OperationName.READ_ACCOUNT))
    assert decision.needs_approval is False
    assert decision.rule.number == 1


@pytest.mark.parametrize("amount", ["0.01", "99.99", "100.00"])
def test_small_credit_runs_on_its_own(amount: str) -> None:
    decision = evaluate(call(OperationName.APPLY_CREDIT, amount))
    assert decision.needs_approval is False
    assert decision.rule.number == 2


@pytest.mark.parametrize("amount", ["100.01", "250.00"])
def test_large_credit_needs_finance(amount: str) -> None:
    decision = evaluate(call(OperationName.APPLY_CREDIT, amount))
    assert decision.required_role is Role.FINANCE
    assert decision.rule.number == 3


def test_debit_needs_finance() -> None:
    decision = evaluate(call(OperationName.APPLY_DEBIT, "1.00"))
    assert decision.required_role is Role.FINANCE
    assert decision.rule.number == 4


@pytest.mark.parametrize(
    "operation", [OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT]
)
def test_freeze_changes_need_risk(operation: OperationName) -> None:
    decision = evaluate(call(operation))
    assert decision.required_role is Role.RISK
    assert decision.rule.number == 5


def test_notify_falls_through_to_the_last_rule() -> None:
    decision = evaluate(call(OperationName.NOTIFY_CUSTOMER))
    assert decision.needs_approval is False
    assert decision.rule.number == 6

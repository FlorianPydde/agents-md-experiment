from decimal import Decimal

import pytest

from app.domain import Money
from app.enums import OperationName, Role
from app.operations import AccountArgs, AmountArgs, NotifyArgs
from app.enums import Origin
from app.policy import PolicyQuery, decide


def query(operation: OperationName, amount: str | None = None) -> PolicyQuery:
    if amount is not None:
        return PolicyQuery(operation, AmountArgs("ACC-100", Money(Decimal(amount))))
    if operation is OperationName.NOTIFY_CUSTOMER:
        return PolicyQuery(operation, NotifyArgs("ACC-100", Origin.INTERNAL))
    return PolicyQuery(operation, AccountArgs("ACC-100"))


def test_read_runs_alone() -> None:
    outcome = decide(query(OperationName.READ_ACCOUNT))
    assert outcome.required_role is None
    assert not outcome.needs_approval


@pytest.mark.parametrize("amount", ["0.00", "99.99", "100.00"])
def test_small_credit_runs_alone(amount: str) -> None:
    assert decide(query(OperationName.APPLY_CREDIT, amount)).required_role is None


@pytest.mark.parametrize("amount", ["100.01", "250.00"])
def test_large_credit_needs_finance(amount: str) -> None:
    assert decide(query(OperationName.APPLY_CREDIT, amount)).required_role is Role.FINANCE


def test_debit_needs_finance() -> None:
    assert decide(query(OperationName.APPLY_DEBIT, "1.00")).required_role is Role.FINANCE


@pytest.mark.parametrize(
    "operation", [OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT]
)
def test_freezing_needs_risk(operation: OperationName) -> None:
    assert decide(query(operation)).required_role is Role.RISK


def test_anything_else_runs_alone() -> None:
    outcome = decide(query(OperationName.NOTIFY_CUSTOMER))
    assert outcome.required_role is None
    assert outcome.rule == "anything else runs on its own"

"""Tests for the policy rules."""

from decimal import Decimal

from app.domain import ApproverRole, Materiality, Money, OperationName
from app.policy import Autonomous, NeedsApproval, PolicyContext, decide


def _ctx(operation: OperationName, materiality: Materiality, amount: str) -> PolicyContext:
    return PolicyContext(operation=operation, materiality=materiality, amount=Money.parse(amount))


def test_read_operation_is_always_autonomous() -> None:
    outcome = decide(_ctx(OperationName.READ_ACCOUNT, Materiality.READ, "999.00"))
    assert isinstance(outcome, Autonomous)


def test_small_credit_is_autonomous() -> None:
    outcome = decide(_ctx(OperationName.APPLY_CREDIT, Materiality.WRITE, "100.00"))
    assert isinstance(outcome, Autonomous)


def test_large_credit_needs_finance() -> None:
    outcome = decide(_ctx(OperationName.APPLY_CREDIT, Materiality.WRITE, "100.01"))
    assert isinstance(outcome, NeedsApproval)
    assert outcome.role is ApproverRole.FINANCE


def test_debit_always_needs_finance() -> None:
    outcome = decide(_ctx(OperationName.APPLY_DEBIT, Materiality.WRITE, "0.01"))
    assert isinstance(outcome, NeedsApproval)
    assert outcome.role is ApproverRole.FINANCE


def test_freeze_needs_risk() -> None:
    outcome = decide(_ctx(OperationName.FREEZE_ACCOUNT, Materiality.WRITE, "0.00"))
    assert isinstance(outcome, NeedsApproval)
    assert outcome.role is ApproverRole.RISK


def test_unfreeze_needs_risk() -> None:
    outcome = decide(_ctx(OperationName.UNFREEZE_ACCOUNT, Materiality.WRITE, "0.00"))
    assert isinstance(outcome, NeedsApproval)
    assert outcome.role is ApproverRole.RISK


def test_notify_customer_is_autonomous() -> None:
    outcome = decide(_ctx(OperationName.NOTIFY_CUSTOMER, Materiality.WRITE, "0.00"))
    assert isinstance(outcome, Autonomous)


def test_boundary_amount_is_exact() -> None:
    at_limit = decide(_ctx(OperationName.APPLY_CREDIT, Materiality.WRITE, "100.00"))
    above_limit = decide(_ctx(OperationName.APPLY_CREDIT, Materiality.WRITE, "100.001"))
    assert isinstance(at_limit, Autonomous)
    assert isinstance(above_limit, NeedsApproval)

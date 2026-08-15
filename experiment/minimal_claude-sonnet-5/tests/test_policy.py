"""Tests for the policy rules (SPEC.md 'Policy' section)."""

from decimal import Decimal

from app.domain import ApproverRole, OperationName
from app.policy import decide


def test_read_runs_on_its_own():
    d = decide(OperationName.READ_ACCOUNT, Decimal("999999.00"))
    assert d.needs_approval is False
    assert d.rule == "read-auto"


def test_apply_credit_at_or_below_limit_runs_on_its_own():
    d = decide(OperationName.APPLY_CREDIT, Decimal("100.00"))
    assert d.needs_approval is False
    assert d.rule == "apply-credit-auto"

    d2 = decide(OperationName.APPLY_CREDIT, Decimal("1.00"))
    assert d2.needs_approval is False


def test_apply_credit_above_limit_needs_finance():
    d = decide(OperationName.APPLY_CREDIT, Decimal("100.01"))
    assert d.needs_approval is True
    assert d.required_role is ApproverRole.FINANCE
    assert d.rule == "apply-credit-finance"


def test_apply_debit_always_needs_finance():
    d = decide(OperationName.APPLY_DEBIT, Decimal("0.01"))
    assert d.needs_approval is True
    assert d.required_role is ApproverRole.FINANCE

    d2 = decide(OperationName.APPLY_DEBIT, Decimal("1000000.00"))
    assert d2.needs_approval is True
    assert d2.required_role is ApproverRole.FINANCE


def test_freeze_and_unfreeze_need_risk():
    d = decide(OperationName.FREEZE_ACCOUNT, Decimal("0"))
    assert d.needs_approval is True
    assert d.required_role is ApproverRole.RISK

    d2 = decide(OperationName.UNFREEZE_ACCOUNT, Decimal("0"))
    assert d2.needs_approval is True
    assert d2.required_role is ApproverRole.RISK


def test_notify_customer_runs_on_its_own_by_default_rule():
    d = decide(OperationName.NOTIFY_CUSTOMER, Decimal("0"))
    assert d.needs_approval is False
    assert d.rule == "default-auto"

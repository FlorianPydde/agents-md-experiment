"""Tests for the policy rule table (SPEC: policy rules)."""

from decimal import Decimal

from app.policy import decide


def test_read_operation_runs_on_its_own():
    result = decide("read_account", "read", {})
    assert result.requires_approval is False


def test_apply_credit_at_or_below_limit_runs_on_its_own():
    result = decide("apply_credit", "write", {"amount": Decimal("100.00")})
    assert result.requires_approval is False

    result = decide("apply_credit", "write", {"amount": Decimal("50.00")})
    assert result.requires_approval is False


def test_apply_credit_above_limit_needs_finance():
    result = decide("apply_credit", "write", {"amount": Decimal("100.01")})
    assert result.requires_approval is True
    assert result.role == "finance"


def test_apply_debit_always_needs_finance():
    result = decide("apply_debit", "write", {"amount": Decimal("1.00")})
    assert result.requires_approval is True
    assert result.role == "finance"

    result = decide("apply_debit", "write", {"amount": Decimal("100000.00")})
    assert result.requires_approval is True
    assert result.role == "finance"


def test_freeze_and_unfreeze_need_risk():
    assert decide("freeze_account", "write", {}).role == "risk"
    assert decide("unfreeze_account", "write", {}).role == "risk"


def test_notify_customer_runs_on_its_own():
    result = decide("notify_customer", "write", {"origin": "internal"})
    assert result.requires_approval is False

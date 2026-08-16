"""Tests for the policy rules."""

from decimal import Decimal

from app.policy import decide


def test_read_operation_runs_on_its_own():
    assert decide("read_account", {}) is None


def test_apply_credit_at_or_below_limit_runs_on_its_own():
    assert decide("apply_credit", {"amount": Decimal("100.00")}) is None
    assert decide("apply_credit", {"amount": Decimal("50.00")}) is None


def test_apply_credit_above_limit_needs_finance():
    assert decide("apply_credit", {"amount": Decimal("100.01")}) == "finance"
    assert decide("apply_credit", {"amount": Decimal("250.00")}) == "finance"


def test_apply_debit_always_needs_finance():
    assert decide("apply_debit", {"amount": Decimal("0.01")}) == "finance"
    assert decide("apply_debit", {"amount": Decimal("10000.00")}) == "finance"


def test_freeze_and_unfreeze_need_risk():
    assert decide("freeze_account", {}) == "risk"
    assert decide("unfreeze_account", {}) == "risk"


def test_notify_customer_runs_on_its_own():
    assert decide("notify_customer", {"origin": "internal"}) is None

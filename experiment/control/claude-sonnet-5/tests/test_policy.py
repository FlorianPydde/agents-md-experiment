from decimal import Decimal

from app.policy import evaluate


def test_read_operation_runs_on_its_own():
    decision = evaluate("read_account", None)
    assert decision.auto is True
    assert decision.role is None


def test_small_credit_runs_on_its_own():
    decision = evaluate("apply_credit", Decimal("100.00"))
    assert decision.auto is True


def test_large_credit_needs_finance():
    decision = evaluate("apply_credit", Decimal("100.01"))
    assert decision.auto is False
    assert decision.role == "finance"


def test_debit_always_needs_finance():
    decision = evaluate("apply_debit", Decimal("1.00"))
    assert decision.auto is False
    assert decision.role == "finance"


def test_freeze_and_unfreeze_need_risk():
    assert evaluate("freeze_account", None).role == "risk"
    assert evaluate("unfreeze_account", None).role == "risk"


def test_notify_customer_runs_on_its_own():
    decision = evaluate("notify_customer", None)
    assert decision.auto is True

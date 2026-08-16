"""Tests for the operations: checks and effects."""

from decimal import Decimal

import pytest

from app.errors import OperationError
from app.operations import run_operation


def make_account(balance="100.00", frozen=False, tier="standard"):
    return {"balance": Decimal(balance), "tier": tier, "frozen": frozen}


def test_apply_debit_fails_when_balance_below_amount():
    account = make_account(balance="10.00")
    with pytest.raises(OperationError):
        run_operation("apply_debit", account, {"amount": Decimal("20.00")})
    # Balance is unchanged after a failed operation.
    assert account["balance"] == Decimal("10.00")


def test_apply_debit_succeeds_when_balance_is_sufficient():
    account = make_account(balance="50.00")
    result = run_operation("apply_debit", account, {"amount": Decimal("20.00")})
    assert account["balance"] == Decimal("30.00")
    assert "debited" in result.summary


def test_write_operations_fail_when_frozen_except_unfreeze():
    account = make_account(frozen=True)
    with pytest.raises(OperationError):
        run_operation("apply_credit", account, {"amount": Decimal("10.00")})
    with pytest.raises(OperationError):
        run_operation("apply_debit", account, {"amount": Decimal("10.00")})
    with pytest.raises(OperationError):
        run_operation("notify_customer", account, {"origin": "internal"})

    # unfreeze_account is the only write operation allowed while frozen.
    result = run_operation("unfreeze_account", account, {})
    assert account["frozen"] is False
    assert "unfrozen" in result.summary


def test_read_account_never_changes_state():
    account = make_account(balance="42.00", frozen=True)
    run_operation("read_account", account, {})
    assert account["balance"] == Decimal("42.00")
    assert account["frozen"] is True


def test_notify_customer_uses_origin_template():
    account = make_account()
    internal = run_operation("notify_customer", account, {"origin": "internal"})
    external = run_operation("notify_customer", account, {"origin": "external"})
    assert internal.data["message"] != external.data["message"]


def test_notify_customer_rejects_unknown_origin():
    account = make_account()
    with pytest.raises(OperationError):
        run_operation("notify_customer", account, {"origin": "postal"})

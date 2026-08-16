import pytest

from app.operations import OperationFailed, run_operation
from decimal import Decimal


def make_account(**overrides):
    account = {
        "id": "ACC-1",
        "owner": "Ada Lovelace",
        "tier": "standard",
        "balance": Decimal("100.00"),
        "frozen": False,
    }
    account.update(overrides)
    return account


def make_request(**overrides):
    request = {
        "reference": "REQ-1",
        "amount": Decimal("10.00"),
        "requester_origin": "internal",
    }
    request.update(overrides)
    return request


def test_read_account_reports_state_without_mutating():
    account = make_account(frozen=True)
    result = run_operation("read_account", account, make_request())
    assert result["balance"] == "100.00"
    assert result["frozen"] is True
    assert account["balance"] == Decimal("100.00")


def test_apply_credit_increases_balance():
    account = make_account()
    run_operation("apply_credit", account, make_request(amount=Decimal("25.00")))
    assert account["balance"] == Decimal("125.00")


def test_apply_credit_fails_when_frozen():
    account = make_account(frozen=True)
    with pytest.raises(OperationFailed):
        run_operation("apply_credit", account, make_request(amount=Decimal("25.00")))


def test_apply_debit_decreases_balance():
    account = make_account()
    run_operation("apply_debit", account, make_request(amount=Decimal("40.00")))
    assert account["balance"] == Decimal("60.00")


def test_apply_debit_fails_when_balance_too_low():
    account = make_account(balance=Decimal("10.00"))
    with pytest.raises(OperationFailed):
        run_operation("apply_debit", account, make_request(amount=Decimal("40.00")))
    # Balance is unchanged after a failed debit.
    assert account["balance"] == Decimal("10.00")


def test_apply_debit_fails_when_frozen():
    account = make_account(frozen=True)
    with pytest.raises(OperationFailed):
        run_operation("apply_debit", account, make_request(amount=Decimal("1.00")))


def test_unfreeze_clears_frozen_even_when_frozen():
    account = make_account(frozen=True)
    run_operation("unfreeze_account", account, make_request())
    assert account["frozen"] is False


def test_freeze_fails_when_already_frozen():
    account = make_account(frozen=True)
    with pytest.raises(OperationFailed):
        run_operation("freeze_account", account, make_request())


def test_freeze_succeeds_when_not_frozen():
    account = make_account(frozen=False)
    run_operation("freeze_account", account, make_request())
    assert account["frozen"] is True


def test_notify_customer_uses_origin_template():
    account = make_account()
    internal = run_operation(
        "notify_customer", account, make_request(requester_origin="internal")
    )
    external = run_operation(
        "notify_customer", account, make_request(requester_origin="external")
    )
    assert internal["message"] != external["message"]

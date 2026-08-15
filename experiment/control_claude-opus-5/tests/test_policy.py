from decimal import Decimal

import pytest

from app import operations, policy
from app.models import Role


def verdict(name: str, amount: str):
    return policy.evaluate(operations.get(name), Decimal(amount))


def test_read_runs_on_its_own() -> None:
    assert verdict("read_account", "9999.00").autonomous


@pytest.mark.parametrize("amount", ["0.00", "1.00", "99.99", "100.00"])
def test_small_credit_runs_on_its_own(amount: str) -> None:
    result = verdict("apply_credit", amount)
    assert result.autonomous
    assert result.rule == "small_credit_is_autonomous"


@pytest.mark.parametrize("amount", ["100.01", "250.00"])
def test_large_credit_needs_finance(amount: str) -> None:
    result = verdict("apply_credit", amount)
    assert result.required_role is Role.FINANCE
    assert result.rule == "large_credit_needs_finance"


@pytest.mark.parametrize("amount", ["0.01", "1000.00"])
def test_debit_always_needs_finance(amount: str) -> None:
    assert verdict("apply_debit", amount).required_role is Role.FINANCE


@pytest.mark.parametrize("name", ["freeze_account", "unfreeze_account"])
def test_freeze_state_needs_risk(name: str) -> None:
    result = verdict(name, "0.00")
    assert result.required_role is Role.RISK
    assert result.rule == "freeze_state_needs_risk"


def test_notify_falls_through_to_the_default_rule() -> None:
    result = verdict("notify_customer", "500.00")
    assert result.autonomous
    assert result.rule == "default_autonomous"


def test_rules_are_exposed_as_data() -> None:
    names = [rule["name"] for rule in policy.rules_json()]
    assert names == [
        "read_is_autonomous",
        "small_credit_is_autonomous",
        "large_credit_needs_finance",
        "debit_needs_finance",
        "freeze_state_needs_risk",
        "default_autonomous",
    ]

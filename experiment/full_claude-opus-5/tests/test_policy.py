from __future__ import annotations

import pytest

from app.enums import OperationName, Role
from app.operations import OperationArgs
from app.policy import RULES, decide
from app.values import Money


def args(amount: str = "10.00") -> OperationArgs:
    return OperationArgs(account_id="ACC-100", amount=Money.parse(amount))


def test_read_runs_alone() -> None:
    verdict = decide(OperationName.READ_ACCOUNT, args())
    assert verdict.runs_alone
    assert verdict.required_role is None


@pytest.mark.parametrize("amount", ["0.00", "99.99", "100.00"])
def test_small_credit_runs_alone(amount: str) -> None:
    assert decide(OperationName.APPLY_CREDIT, args(amount)).runs_alone


@pytest.mark.parametrize("amount", ["100.01", "250.00"])
def test_large_credit_needs_finance(amount: str) -> None:
    verdict = decide(OperationName.APPLY_CREDIT, args(amount))
    assert verdict.required_role is Role.FINANCE


def test_debit_always_needs_finance() -> None:
    assert decide(OperationName.APPLY_DEBIT, args("1.00")).required_role is Role.FINANCE


@pytest.mark.parametrize(
    "operation", [OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT]
)
def test_freeze_changes_need_risk(operation: OperationName) -> None:
    assert decide(operation, args()).required_role is Role.RISK


def test_notify_falls_through_to_catch_all() -> None:
    verdict = decide(OperationName.NOTIFY_CUSTOMER, args())
    assert verdict.runs_alone
    assert verdict.rule == RULES[-1].description


def test_first_matching_rule_wins() -> None:
    # apply_credit matches rule 2 and rule 3; the small case must take rule 2.
    assert decide(OperationName.APPLY_CREDIT, args("100.00")).rule == RULES[1].description

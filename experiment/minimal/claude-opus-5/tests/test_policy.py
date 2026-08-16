from __future__ import annotations

from decimal import Decimal

import pytest

from app import policy
from app.domain import OperationName, Role


@pytest.mark.parametrize(
    ("operation", "amount", "expected_role", "expected_rule"),
    [
        (OperationName.READ_ACCOUNT, Decimal("1000.00"), None, "read_runs_alone"),
        (OperationName.APPLY_CREDIT, Decimal("99.99"), None, "small_credit_runs_alone"),
        (OperationName.APPLY_CREDIT, Decimal("100.00"), None, "small_credit_runs_alone"),
        (OperationName.APPLY_CREDIT, Decimal("100.01"), Role.FINANCE, "large_credit_needs_finance"),
        (OperationName.APPLY_DEBIT, Decimal("1.00"), Role.FINANCE, "debit_needs_finance"),
        (OperationName.FREEZE_ACCOUNT, Decimal("0.00"), Role.RISK, "freeze_change_needs_risk"),
        (OperationName.UNFREEZE_ACCOUNT, Decimal("0.00"), Role.RISK, "freeze_change_needs_risk"),
        (OperationName.NOTIFY_CUSTOMER, Decimal("0.00"), None, "default_runs_alone"),
    ],
)
def test_rules_pick_the_first_match(
    operation: OperationName, amount: Decimal, expected_role: Role | None, expected_rule: str
) -> None:
    decision = policy.evaluate(operation, amount)

    assert decision.required_role is expected_role
    assert decision.rule == expected_rule
    assert decision.runs_alone is (expected_role is None)


def test_every_rule_names_only_approving_roles() -> None:
    for rule in policy.RULES:
        assert rule.required_role is None or rule.required_role in tuple(Role)

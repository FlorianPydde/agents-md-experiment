from decimal import Decimal

import pytest

from app.models import Materiality, OperationName, Role
from app.workflow import evaluate_policy


@pytest.mark.parametrize(
    ("operation", "materiality", "amount", "autonomous", "role"),
    [
        (OperationName.READ_ACCOUNT, Materiality.READ, "999.00", True, None),
        (OperationName.APPLY_CREDIT, Materiality.WRITE, "100.00", True, None),
        (
            OperationName.APPLY_CREDIT,
            Materiality.WRITE,
            "100.01",
            False,
            Role.FINANCE,
        ),
        (
            OperationName.APPLY_DEBIT,
            Materiality.WRITE,
            "1.00",
            False,
            Role.FINANCE,
        ),
        (
            OperationName.FREEZE_ACCOUNT,
            Materiality.WRITE,
            "0.00",
            False,
            Role.RISK,
        ),
        (
            OperationName.UNFREEZE_ACCOUNT,
            Materiality.WRITE,
            "0.00",
            False,
            Role.RISK,
        ),
        (
            OperationName.NOTIFY_CUSTOMER,
            Materiality.WRITE,
            "0.00",
            True,
            None,
        ),
    ],
)
def test_first_matching_policy_rule(
    operation, materiality, amount, autonomous, role
):
    result = evaluate_policy(operation, materiality, Decimal(amount))
    assert result.autonomous is autonomous
    assert result.required_role is role

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain import ApprovalRole, Materiality, OperationName
from app.engine import decide_policy


@pytest.mark.parametrize(
    ("operation", "materiality", "amount", "required_role"),
    [
        (
            OperationName.READ_ACCOUNT,
            Materiality.READ,
            "999.00",
            None,
        ),
        (
            OperationName.APPLY_CREDIT,
            Materiality.WRITE,
            "100.00",
            None,
        ),
        (
            OperationName.APPLY_CREDIT,
            Materiality.WRITE,
            "100.01",
            ApprovalRole.FINANCE,
        ),
        (
            OperationName.APPLY_DEBIT,
            Materiality.WRITE,
            "1.00",
            ApprovalRole.FINANCE,
        ),
        (
            OperationName.FREEZE_ACCOUNT,
            Materiality.WRITE,
            "1.00",
            ApprovalRole.RISK,
        ),
        (
            OperationName.UNFREEZE_ACCOUNT,
            Materiality.WRITE,
            "1.00",
            ApprovalRole.RISK,
        ),
        (
            OperationName.NOTIFY_CUSTOMER,
            Materiality.WRITE,
            "1.00",
            None,
        ),
    ],
)
def test_policy_first_matching_rule(
    operation: OperationName,
    materiality: Materiality,
    amount: str,
    required_role: ApprovalRole | None,
) -> None:
    outcome = decide_policy(operation, materiality, Decimal(amount))

    assert outcome.required_role is required_role

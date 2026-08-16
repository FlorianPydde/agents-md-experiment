"""Tests for the policy engine (SPEC.md "Policy" section)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain import decide_policy


@pytest.mark.parametrize(
    "operation,amount",
    [("read_account", None)],
)
def test_read_operations_run_on_their_own(operation, amount):
    decision = decide_policy(operation, amount)
    assert decision.auto is True
    assert decision.role is None


def test_small_goodwill_credit_runs_on_its_own():
    decision = decide_policy("apply_credit", Decimal("100.00"))
    assert decision.auto is True
    assert decision.role is None


def test_small_goodwill_credit_boundary_is_inclusive():
    decision = decide_policy("apply_credit", Decimal("100.00"))
    assert decision.auto is True


def test_large_goodwill_credit_needs_finance():
    decision = decide_policy("apply_credit", Decimal("100.01"))
    assert decision.auto is False
    assert decision.role == "finance"


def test_apply_debit_always_needs_finance():
    decision = decide_policy("apply_debit", Decimal("0.01"))
    assert decision.auto is False
    assert decision.role == "finance"

    decision = decide_policy("apply_debit", Decimal("1000000.00"))
    assert decision.auto is False
    assert decision.role == "finance"


@pytest.mark.parametrize("operation", ["freeze_account", "unfreeze_account"])
def test_freeze_and_unfreeze_need_risk(operation):
    decision = decide_policy(operation, None)
    assert decision.auto is False
    assert decision.role == "risk"


def test_notify_customer_runs_on_its_own():
    decision = decide_policy("notify_customer", None)
    assert decision.auto is True
    assert decision.role is None

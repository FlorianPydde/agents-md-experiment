"""Tests for operations, including a failing one (SPEC.md deliverable)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.scenario import DecideAction, IntakeAction, Requester


def intake(reference, kind, account, amount, origin="internal"):
    return IntakeAction(
        reference=reference,
        kind=kind,
        account=account,
        amount=Decimal(amount),
        requester=Requester(name="Test", role="agent", origin=origin),
    )


def test_apply_debit_fails_when_account_frozen(engine):
    # ACC-200 starts frozen; collect_debt needs finance approval, then fails.
    engine.intake(intake("R1", "collect_debt", "ACC-200", "10.00", origin="external"))
    engine.decide(DecideAction(reference="R1", role="finance", decision="approve"))

    row = engine.get_request("R1")
    assert row["state"] == "failed"
    steps = {s["step_index"]: s["state"] for s in engine.get_steps("R1")}
    assert steps[1] == "failed"
    assert steps[2] == "skipped"
    # Balance is untouched by the failed debit.
    assert engine.accounts["ACC-200"].balance == Decimal("50.00")


def test_apply_debit_fails_when_balance_insufficient(engine):
    # Unfreeze first so the failure is specifically about balance.
    engine.accounts["ACC-200"].frozen = False
    engine.intake(intake("R1", "collect_debt", "ACC-200", "999.00", origin="external"))
    engine.decide(DecideAction(reference="R1", role="finance", decision="approve"))

    row = engine.get_request("R1")
    assert row["state"] == "failed"
    assert engine.accounts["ACC-200"].balance == Decimal("50.00")


def test_apply_credit_increases_balance(engine):
    engine.intake(intake("R1", "goodwill_credit", "ACC-100", "50.00"))
    assert engine.accounts["ACC-100"].balance == Decimal("1250.00")


def test_account_recovery_unfreezes_and_credits(engine):
    engine.intake(intake("R1", "account_recovery", "ACC-200", "20.00", origin="external"))
    engine.decide(DecideAction(reference="R1", role="risk", decision="approve"))
    # apply_credit of 20.00 is auto (<= 100.00), so the request should complete.
    row = engine.get_request("R1")
    assert row["state"] == "completed"
    assert engine.accounts["ACC-200"].frozen is False
    assert engine.accounts["ACC-200"].balance == Decimal("70.00")


def test_notify_customer_uses_origin_specific_message(engine):
    engine.intake(intake("R1", "goodwill_credit", "ACC-100", "10.00", origin="internal"))
    engine.intake(intake("R2", "goodwill_credit", "ACC-100", "10.00", origin="external"))
    log1 = [e for e in engine.get_log("R1") if e["event"] == "operation_run"][-1]
    log2 = [e for e in engine.get_log("R2") if e["event"] == "operation_run"][-1]
    assert log1["data"]["result"]["message"] != log2["data"]["result"]["message"]

"""Tests for the approval flow: awaiting approval, approve, reject, and the
wrong-role / nothing-pending error cases."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.errors import AppError
from app.scenario import DecideAction, IntakeAction, Requester


def intake(reference, kind, account, amount, origin="internal"):
    return IntakeAction(
        reference=reference,
        kind=kind,
        account=account,
        amount=Decimal(amount),
        requester=Requester(name="Test", role="agent", origin=origin),
    )


def test_large_credit_stops_for_approval(engine):
    engine.intake(intake("R1", "goodwill_credit", "ACC-100", "250.00"))
    row = engine.get_request("R1")
    assert row["state"] == "awaiting_approval"

    pending = engine.list_pending_approvals()
    assert len(pending) == 1
    assert pending[0]["role_required"] == "finance"
    assert pending[0]["step_index"] == 1


def test_approve_resumes_and_completes(engine):
    engine.intake(intake("R1", "goodwill_credit", "ACC-100", "250.00"))
    engine.decide(DecideAction(reference="R1", role="finance", decision="approve"))

    row = engine.get_request("R1")
    assert row["state"] == "completed"
    assert engine.accounts["ACC-100"].balance == Decimal("1450.00")
    assert engine.list_pending_approvals() == []


def test_reject_stops_the_request(engine):
    engine.intake(intake("R1", "account_recovery", "ACC-200", "75.00", origin="external"))
    row = engine.get_request("R1")
    assert row["state"] == "awaiting_approval"

    engine.decide(DecideAction(reference="R1", role="risk", decision="reject"))
    row = engine.get_request("R1")
    assert row["state"] == "rejected"
    # The account was never unfrozen or credited.
    assert engine.accounts["ACC-200"].frozen is True
    assert engine.accounts["ACC-200"].balance == Decimal("50.00")

    steps = {s["step_index"]: s["state"] for s in engine.get_steps("R1")}
    assert steps[1] == "skipped"
    assert steps[2] == "skipped"
    assert steps[3] == "skipped"


def test_decide_with_wrong_role_is_rejected_as_error(engine):
    engine.intake(intake("R1", "goodwill_credit", "ACC-100", "250.00"))
    with pytest.raises(AppError):
        engine.decide(DecideAction(reference="R1", role="risk", decision="approve"))
    # The request is still waiting; the bad decision did not resolve it.
    assert engine.get_request("R1")["state"] == "awaiting_approval"


def test_decide_with_nothing_pending_is_an_error(engine):
    engine.intake(intake("R1", "goodwill_credit", "ACC-100", "50.00"))
    assert engine.get_request("R1")["state"] == "completed"
    with pytest.raises(AppError):
        engine.decide(DecideAction(reference="R1", role="finance", decision="approve"))


def test_unknown_account_is_an_error(engine):
    with pytest.raises(AppError):
        engine.intake(intake("R1", "goodwill_credit", "ACC-999", "50.00"))


def test_duplicate_reference_is_an_error(engine):
    engine.intake(intake("R1", "goodwill_credit", "ACC-100", "10.00"))
    with pytest.raises(AppError):
        engine.intake(intake("R1", "goodwill_credit", "ACC-100", "10.00"))

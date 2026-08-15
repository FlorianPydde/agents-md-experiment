"""End-to-end tests driving the Engine directly against a fresh in-memory-like
Store (backed by a temp file), covering the approval flow and a failing
operation.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.engine import Engine
from app.loaders import AccountSeed, DecideStep, IntakeRequest, IntakeStep, Requester
from app.store import Store


@pytest.fixture
def engine(tmp_path: Path):
    store = Store.fresh(tmp_path / "db.sqlite3")
    eng = Engine(store)
    eng.seed_world(
        [
            AccountSeed(
                id="ACC-1", owner="A", tier="standard", balance=Decimal("1000.00"), frozen=False
            ),
            AccountSeed(
                id="ACC-2", owner="B", tier="standard", balance=Decimal("10.00"), frozen=True
            ),
        ]
    )
    yield eng
    store.close()


def _intake(reference, kind, account, amount, origin="internal", role="agent"):
    return IntakeStep(
        request=IntakeRequest(
            reference=reference,
            kind=kind,
            account=account,
            amount=Decimal(amount),
            requester=Requester(name="Tester", role=role, origin=origin),
        )
    )


def test_small_goodwill_credit_completes_without_approval(engine):
    engine.intake(_intake("R1", "goodwill_credit", "ACC-1", "50.00"))
    row = engine.store.get_request("R1")
    assert row["state"] == "completed"
    account = engine.store.get_account("ACC-1")
    assert account.balance == Decimal("1050.00")


def test_large_goodwill_credit_awaits_finance_then_completes_on_approve(engine):
    engine.intake(_intake("R2", "goodwill_credit", "ACC-1", "250.00"))
    row = engine.store.get_request("R2")
    assert row["state"] == "awaiting_approval"

    approval = engine.store.find_pending_approval("R2")
    assert approval["role"] == "finance"

    engine.decide(DecideStep(reference="R2", role="finance", decision="approve"))
    row = engine.store.get_request("R2")
    assert row["state"] == "completed"
    account = engine.store.get_account("ACC-1")
    assert account.balance == Decimal("1250.00")


def test_rejecting_an_approval_stops_the_request(engine):
    engine.intake(_intake("R3", "account_recovery", "ACC-2", "20.00"))
    row = engine.store.get_request("R3")
    assert row["state"] == "awaiting_approval"

    approval = engine.store.find_pending_approval("R3")
    assert approval["role"] == "risk"

    engine.decide(DecideStep(reference="R3", role="risk", decision="reject"))
    row = engine.store.get_request("R3")
    assert row["state"] == "rejected"

    # Account must be unchanged: still frozen, balance untouched.
    account = engine.store.get_account("ACC-2")
    assert account.frozen is True
    assert account.balance == Decimal("10.00")


def test_wrong_role_deciding_is_rejected(engine):
    engine.intake(_intake("R4", "account_recovery", "ACC-2", "20.00"))
    with pytest.raises(Exception):
        engine.decide(DecideStep(reference="R4", role="finance", decision="approve"))


def test_decide_with_nothing_pending_is_rejected(engine):
    engine.intake(_intake("R5", "goodwill_credit", "ACC-1", "1.00"))
    # R5 auto-completes; nothing pending.
    with pytest.raises(Exception):
        engine.decide(DecideStep(reference="R5", role="finance", decision="approve"))


def test_debit_exceeding_balance_fails_and_stops_the_plan(engine):
    # Debit of an amount larger than the balance, approved by finance,
    # must fail at apply_debit and never reach notify_customer.
    engine.intake(_intake("R6", "collect_debt", "ACC-1", "5000.00"))
    row = engine.store.get_request("R6")
    assert row["state"] == "awaiting_approval"

    engine.decide(DecideStep(reference="R6", role="finance", decision="approve"))
    row = engine.store.get_request("R6")
    assert row["state"] == "failed"

    steps = engine.store.list_steps("R6")
    assert steps[1]["operation"] == "apply_debit"
    assert steps[1]["state"] == "failed"
    assert steps[2]["state"] == "pending"  # notify_customer never ran

    account = engine.store.get_account("ACC-1")
    assert account.balance == Decimal("1000.00")  # untouched


def test_write_operation_on_frozen_account_fails(engine):
    # goodwill_credit on the frozen ACC-2: read_account runs fine, but
    # apply_credit (a write) must fail because the account is frozen.
    engine.intake(_intake("R7", "goodwill_credit", "ACC-2", "5.00"))
    row = engine.store.get_request("R7")
    assert row["state"] == "failed"

    steps = engine.store.list_steps("R7")
    assert steps[0]["operation"] == "read_account"
    assert steps[0]["state"] == "completed"
    assert steps[1]["operation"] == "apply_credit"
    assert steps[1]["state"] == "failed"


def test_unfreeze_then_credit_flow_for_account_recovery(engine):
    engine.intake(_intake("R8", "account_recovery", "ACC-2", "5.00"))
    engine.decide(DecideStep(reference="R8", role="risk", decision="approve"))
    row = engine.store.get_request("R8")
    # unfreeze needs risk approval; apply_credit of 5.00 is auto (<=100).
    assert row["state"] == "completed"
    account = engine.store.get_account("ACC-2")
    assert account.frozen is False
    assert account.balance == Decimal("15.00")

"""Tests for the approval flow and failing operations, driven through the
engine against an in-memory-like store (a temp SQLite file)."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.domain import (
    Account,
    ApproverRole,
    Decision,
    DecideEvent,
    Origin,
    Requester,
    RequestKind,
    RequestState,
    Role,
    ServiceRequest,
    StepState,
    Tier,
    World,
)
from app.engine import decide, intake
from app.errors import AppError
from app.store import Store


@pytest.fixture
def world() -> World:
    return World(
        accounts={
            "ACC-1": Account(id="ACC-1", owner="Owner One", tier=Tier.STANDARD, balance=Decimal("1000.00"), frozen=False),
            "ACC-2": Account(id="ACC-2", owner="Owner Two", tier=Tier.PREMIUM, balance=Decimal("10.00"), frozen=True),
        }
    )


@pytest.fixture
def store(tmp_path: Path, world: World) -> Store:
    s = Store.create_fresh(tmp_path / "test.db")
    for account in world.accounts.values():
        s.put_account(account)
    s.commit()
    return s


def _requester(origin: Origin = Origin.INTERNAL) -> Requester:
    return Requester(name="Test Agent", role=Role.AGENT, origin=origin)


def test_large_goodwill_credit_awaits_finance_then_completes(store: Store, world: World):
    req = ServiceRequest(
        reference="REQ-A",
        kind=RequestKind.GOODWILL_CREDIT,
        account="ACC-1",
        amount=Decimal("250.00"),
        requester=_requester(),
    )
    intake(store, world, req, order_index=0)

    stored = store.get_request("REQ-A")
    assert stored.state is RequestState.AWAITING_APPROVAL

    pending = store.get_pending_approval("REQ-A")
    assert pending is not None
    assert pending.required_role is ApproverRole.FINANCE

    decide(store, DecideEvent(reference="REQ-A", role=ApproverRole.FINANCE, decision=Decision.APPROVE))

    stored = store.get_request("REQ-A")
    assert stored.state is RequestState.COMPLETED
    account = store.get_account("ACC-1")
    assert account.balance == Decimal("1250.00")


def test_rejecting_an_approval_stops_the_request(store: Store, world: World):
    req = ServiceRequest(
        reference="REQ-B",
        kind=RequestKind.GOODWILL_CREDIT,
        account="ACC-1",
        amount=Decimal("500.00"),
        requester=_requester(),
    )
    intake(store, world, req, order_index=0)
    assert store.get_request("REQ-B").state is RequestState.AWAITING_APPROVAL

    decide(store, DecideEvent(reference="REQ-B", role=ApproverRole.FINANCE, decision=Decision.REJECT))

    stored = store.get_request("REQ-B")
    assert stored.state is RequestState.REJECTED
    # balance unchanged
    account = store.get_account("ACC-1")
    assert account.balance == Decimal("1000.00")


def test_wrong_role_deciding_is_rejected(store: Store, world: World):
    req = ServiceRequest(
        reference="REQ-C",
        kind=RequestKind.GOODWILL_CREDIT,
        account="ACC-1",
        amount=Decimal("500.00"),
        requester=_requester(),
    )
    intake(store, world, req, order_index=0)

    with pytest.raises(AppError):
        decide(store, DecideEvent(reference="REQ-C", role=ApproverRole.RISK, decision=Decision.APPROVE))


def test_deciding_with_nothing_pending_is_rejected(store: Store, world: World):
    req = ServiceRequest(
        reference="REQ-D",
        kind=RequestKind.GOODWILL_CREDIT,
        account="ACC-1",
        amount=Decimal("50.00"),
        requester=_requester(),
    )
    intake(store, world, req, order_index=0)
    assert store.get_request("REQ-D").state is RequestState.COMPLETED

    with pytest.raises(AppError):
        decide(store, DecideEvent(reference="REQ-D", role=ApproverRole.FINANCE, decision=Decision.APPROVE))


def test_debit_exceeding_balance_fails(store: Store, world: World):
    """apply_debit fails when the balance is below the amount (a failing operation)."""
    req = ServiceRequest(
        reference="REQ-E",
        kind=RequestKind.COLLECT_DEBT,
        account="ACC-1",
        amount=Decimal("5000.00"),
        requester=_requester(),
    )
    intake(store, world, req, order_index=0)
    # apply_debit needs finance approval regardless of amount
    decide(store, DecideEvent(reference="REQ-E", role=ApproverRole.FINANCE, decision=Decision.APPROVE))

    stored = store.get_request("REQ-E")
    assert stored.state is RequestState.FAILED

    steps = store.get_steps("REQ-E")
    debit_step = next(s for s in steps if s.operation.value == "apply_debit")
    assert debit_step.state is StepState.FAILED

    # balance unchanged after the failed debit
    account = store.get_account("ACC-1")
    assert account.balance == Decimal("1000.00")


def test_write_operation_fails_on_frozen_account(store: Store, world: World):
    """Every write operation other than unfreeze_account fails when frozen."""
    req = ServiceRequest(
        reference="REQ-F",
        kind=RequestKind.GOODWILL_CREDIT,
        account="ACC-2",  # frozen
        amount=Decimal("10.00"),
        requester=_requester(),
    )
    intake(store, world, req, order_index=0)

    stored = store.get_request("REQ-F")
    assert stored.state is RequestState.FAILED


def test_account_recovery_unfreezes_then_credits(store: Store, world: World):
    req = ServiceRequest(
        reference="REQ-G",
        kind=RequestKind.ACCOUNT_RECOVERY,
        account="ACC-2",
        amount=Decimal("20.00"),
        requester=_requester(),
    )
    intake(store, world, req, order_index=0)
    # unfreeze_account needs risk
    stored = store.get_request("REQ-G")
    assert stored.state is RequestState.AWAITING_APPROVAL
    pending = store.get_pending_approval("REQ-G")
    assert pending.required_role is ApproverRole.RISK

    decide(store, DecideEvent(reference="REQ-G", role=ApproverRole.RISK, decision=Decision.APPROVE))

    stored = store.get_request("REQ-G")
    assert stored.state is RequestState.COMPLETED
    account = store.get_account("ACC-2")
    assert account.frozen is False
    assert account.balance == Decimal("30.00")

"""Tests for the engine: intake, approval flow, and failing operations."""

from pathlib import Path

import pytest

from app.domain import (
    Account,
    ApproverRole,
    Decision,
    Money,
    Origin,
    RequestKind,
    RequestState,
    Requester,
    ServiceRequest,
    StepStatus,
    Tier,
)
from app.engine import EngineError, apply_decision, intake_request
from app.store import Store


def _account(id_: str, balance: str, frozen: bool = False, tier: Tier = Tier.STANDARD) -> Account:
    return Account(id=id_, owner="Owner", tier=tier, balance=Money.parse(balance), frozen=frozen)


def _request(reference: str, kind: RequestKind, account_id: str, amount: str, origin: Origin = Origin.INTERNAL) -> ServiceRequest:
    return ServiceRequest(
        reference=reference,
        kind=kind,
        account_id=account_id,
        amount=Money.parse(amount),
        requester=Requester(name="Agent", role="agent", origin=origin),
    )


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "test.db") as db:
        yield db


def test_small_goodwill_credit_completes_without_approval(store: Store) -> None:
    store.put_account(_account("ACC-1", "100.00"))
    request = _request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-1", "50.00")

    intake_request(store, request, seq=1)

    record = store.get_request("REQ-1")
    assert record is not None
    assert record.state is RequestState.COMPLETED
    account = store.get_account("ACC-1")
    assert account is not None
    assert account.balance.formatted() == "150.00"


def test_large_goodwill_credit_needs_finance_approval(store: Store) -> None:
    store.put_account(_account("ACC-1", "100.00"))
    request = _request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-1", "250.00")

    intake_request(store, request, seq=1)

    record = store.get_request("REQ-1")
    assert record is not None
    assert record.state is RequestState.AWAITING_APPROVAL
    approval = store.get_pending_approval("REQ-1")
    assert approval is not None
    assert approval.required_role is ApproverRole.FINANCE

    apply_decision(store, "REQ-1", ApproverRole.FINANCE, Decision.APPROVE)

    record = store.get_request("REQ-1")
    assert record is not None
    assert record.state is RequestState.COMPLETED
    account = store.get_account("ACC-1")
    assert account is not None
    assert account.balance.formatted() == "350.00"


def test_rejected_approval_stops_the_request(store: Store) -> None:
    store.put_account(_account("ACC-1", "100.00", frozen=True))
    request = _request("REQ-1", RequestKind.ACCOUNT_RECOVERY, "ACC-1", "10.00")

    intake_request(store, request, seq=1)
    record = store.get_request("REQ-1")
    assert record is not None
    assert record.state is RequestState.AWAITING_APPROVAL

    apply_decision(store, "REQ-1", ApproverRole.RISK, Decision.REJECT)

    record = store.get_request("REQ-1")
    assert record is not None
    assert record.state is RequestState.REJECTED
    account = store.get_account("ACC-1")
    assert account is not None
    assert account.frozen is True
    assert account.balance.formatted() == "100.00"

    steps = store.list_steps("REQ-1")
    unfreeze_step = next(s for s in steps if s.index == 1)
    assert unfreeze_step.status is StepStatus.REJECTED


def test_wrong_approver_role_is_rejected_with_error(store: Store) -> None:
    store.put_account(_account("ACC-1", "100.00"))
    request = _request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-1", "250.00")
    intake_request(store, request, seq=1)

    with pytest.raises(EngineError):
        apply_decision(store, "REQ-1", ApproverRole.RISK, Decision.APPROVE)


def test_decision_for_unknown_request_is_an_error(store: Store) -> None:
    with pytest.raises(EngineError):
        apply_decision(store, "REQ-NOPE", ApproverRole.FINANCE, Decision.APPROVE)


def test_decision_with_nothing_pending_is_an_error(store: Store) -> None:
    store.put_account(_account("ACC-1", "100.00"))
    request = _request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-1", "50.00")
    intake_request(store, request, seq=1)  # completes without approval

    with pytest.raises(EngineError):
        apply_decision(store, "REQ-1", ApproverRole.FINANCE, Decision.APPROVE)


def test_debit_fails_when_balance_is_insufficient(store: Store) -> None:
    store.put_account(_account("ACC-1", "10.00"))
    request = _request("REQ-1", RequestKind.COLLECT_DEBT, "ACC-1", "500.00")

    intake_request(store, request, seq=1)
    apply_decision(store, "REQ-1", ApproverRole.FINANCE, Decision.APPROVE)

    record = store.get_request("REQ-1")
    assert record is not None
    assert record.state is RequestState.FAILED
    account = store.get_account("ACC-1")
    assert account is not None
    assert account.balance.formatted() == "10.00"


def test_write_operation_fails_on_frozen_account(store: Store) -> None:
    store.put_account(_account("ACC-1", "1000.00", frozen=True))
    request = _request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-1", "50.00")

    intake_request(store, request, seq=1)

    record = store.get_request("REQ-1")
    assert record is not None
    assert record.state is RequestState.FAILED
    account = store.get_account("ACC-1")
    assert account is not None
    assert account.balance.formatted() == "1000.00"


def test_intake_for_unknown_account_is_an_error(store: Store) -> None:
    request = _request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-MISSING", "10.00")
    with pytest.raises(EngineError):
        intake_request(store, request, seq=1)


def test_duplicate_reference_is_an_error(store: Store) -> None:
    store.put_account(_account("ACC-1", "100.00"))
    request = _request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-1", "10.00")
    intake_request(store, request, seq=1)

    with pytest.raises(EngineError):
        intake_request(store, request, seq=2)

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from app.domain import (
    ApprovalRole,
    ConflictError,
    Decision,
    EventKind,
    Money,
    Origin,
    RequestKind,
    RequestState,
    Requester,
    ServiceRequest,
    StepState,
)
from app.engine import Runner
from app.storage import Store


def make_request(
    reference: str,
    kind: RequestKind,
    amount: str,
) -> ServiceRequest:
    return ServiceRequest(
        reference=reference,
        kind=kind,
        account_id="ACC-1",
        amount=Money(Decimal(amount)),
        requester=Requester("Test Agent", "agent", Origin.INTERNAL),
    )


def test_approval_flow_resumes_and_completes(store: Store) -> None:
    runner = Runner(store)
    runner.intake(
        make_request("REQ-APPROVE", RequestKind.GOODWILL_CREDIT, "250.00")
    )

    waiting = store.get_request("REQ-APPROVE")
    assert waiting is not None
    assert waiting.state is RequestState.AWAITING_APPROVAL
    approval = store.get_pending_approval("REQ-APPROVE")
    assert approval is not None
    assert approval.required_role is ApprovalRole.FINANCE

    with pytest.raises(ConflictError, match="requires decision from finance"):
        runner.decide(
            "REQ-APPROVE", ApprovalRole.RISK, Decision.APPROVE
        )

    runner.decide(
        "REQ-APPROVE", ApprovalRole.FINANCE, Decision.APPROVE
    )

    completed = store.get_request("REQ-APPROVE")
    account = store.get_account("ACC-1")
    assert completed is not None
    assert completed.state is RequestState.COMPLETED
    assert account is not None
    assert account.balance.amount == Decimal("750.00")
    assert all(
        step.state is StepState.COMPLETED
        for step in store.get_steps("REQ-APPROVE")
    )


def test_rejection_stops_remaining_steps(store: Store) -> None:
    runner = Runner(store)
    runner.intake(
        make_request("REQ-REJECT", RequestKind.COLLECT_DEBT, "10.00")
    )

    runner.decide(
        "REQ-REJECT", ApprovalRole.FINANCE, Decision.REJECT
    )

    record = store.get_request("REQ-REJECT")
    states = [step.state for step in store.get_steps("REQ-REJECT")]
    assert record is not None
    assert record.state is RequestState.REJECTED
    assert states == [
        StepState.COMPLETED,
        StepState.SKIPPED,
        StepState.SKIPPED,
    ]


def test_failing_operation_marks_request_failed(store: Store) -> None:
    runner = Runner(store)
    runner.intake(
        make_request("REQ-FAIL", RequestKind.COLLECT_DEBT, "600.00")
    )

    runner.decide("REQ-FAIL", ApprovalRole.FINANCE, Decision.APPROVE)

    record = store.get_request("REQ-FAIL")
    events = store.get_events("REQ-FAIL")
    assert record is not None
    assert record.state is RequestState.FAILED
    assert events[-2]["kind"] == EventKind.OPERATION_FAILED.value
    assert "insufficient balance" in events[-2]["data"]["error"]


def test_event_log_rejects_updates_and_deletes(store: Store) -> None:
    Runner(store).intake(
        make_request("REQ-LOG", RequestKind.GOODWILL_CREDIT, "10.00")
    )

    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            connection.execute(
                "UPDATE event_log SET event_kind = 'changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            connection.execute("DELETE FROM event_log")

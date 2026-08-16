import pytest

from app.domain import DecisionCommand
from app.engine import decide, intake
from app.enums import ApprovalState, Decision, EventKind, RequestKind, RequestState, Role, StepState
from app.errors import ServiceRequestError, UnknownReferenceError
from tests.conftest import make_request


def test_small_credit_completes_without_approval(store) -> None:
    intake(store, make_request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00"))

    assert store.tracked("REQ-1").state is RequestState.COMPLETED
    assert store.account("ACC-100").balance.amount == pytest.approx(1250.00)
    assert store.pending_approvals() == ()


def test_large_credit_waits_for_finance(store) -> None:
    intake(store, make_request("REQ-2", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))

    tracked = store.tracked("REQ-2")
    assert tracked.state is RequestState.AWAITING_APPROVAL
    approval = store.pending_approval("REQ-2")
    assert approval is not None
    assert approval.role is Role.FINANCE
    assert store.step_records("REQ-2")[1].state is StepState.AWAITING_APPROVAL
    assert store.account("ACC-100").balance.amount == pytest.approx(1200.00)


def test_approval_runs_the_step_and_continues(store) -> None:
    intake(store, make_request("REQ-2", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))
    decide(store, DecisionCommand("REQ-2", Role.FINANCE, Decision.APPROVE))

    assert store.tracked("REQ-2").state is RequestState.COMPLETED
    assert store.account("ACC-100").balance.amount == pytest.approx(1450.00)
    assert [approval.state for approval in store.approvals("REQ-2")] == [
        ApprovalState.APPROVED
    ]
    assert all(step.state is StepState.DONE for step in store.step_records("REQ-2"))


def test_rejection_stops_the_run(store) -> None:
    intake(
        store, make_request("REQ-3", RequestKind.ACCOUNT_RECOVERY, "ACC-200", "75.00")
    )
    decide(store, DecisionCommand("REQ-3", Role.RISK, Decision.REJECT))

    assert store.tracked("REQ-3").state is RequestState.REJECTED
    assert store.account("ACC-200").frozen is True
    states = [step.state for step in store.step_records("REQ-3")]
    assert states == [
        StepState.DONE,
        StepState.SKIPPED,
        StepState.PENDING,
        StepState.PENDING,
    ]


def test_decision_from_the_wrong_role_is_refused(store) -> None:
    intake(
        store, make_request("REQ-3", RequestKind.ACCOUNT_RECOVERY, "ACC-200", "75.00")
    )

    with pytest.raises(ServiceRequestError, match="needs approval from risk"):
        decide(store, DecisionCommand("REQ-3", Role.FINANCE, Decision.APPROVE))


def test_decision_with_nothing_pending_is_refused(store) -> None:
    intake(store, make_request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00"))

    with pytest.raises(ServiceRequestError, match="nothing pending"):
        decide(store, DecisionCommand("REQ-1", Role.FINANCE, Decision.APPROVE))


def test_decision_on_an_unknown_request_is_refused(store) -> None:
    with pytest.raises(UnknownReferenceError, match="REQ-404"):
        decide(store, DecisionCommand("REQ-404", Role.FINANCE, Decision.APPROVE))


def test_debit_beyond_the_balance_fails_the_request(store) -> None:
    intake(store, make_request("REQ-4", RequestKind.COLLECT_DEBT, "ACC-200", "500.00"))
    decide(store, DecisionCommand("REQ-4", Role.FINANCE, Decision.APPROVE))

    assert store.tracked("REQ-4").state is RequestState.FAILED
    assert store.account("ACC-200").balance.amount == pytest.approx(50.00)
    kinds = [recorded.entry.kind for recorded in store.entries("REQ-4")]
    assert EventKind.OPERATION_FAILED in kinds
    assert store.step_records("REQ-4")[2].state is StepState.PENDING


def test_a_frozen_account_blocks_a_write(store) -> None:
    intake(store, make_request("REQ-5", RequestKind.GOODWILL_CREDIT, "ACC-200", "10.00"))

    assert store.tracked("REQ-5").state is RequestState.FAILED
    reasons = [
        dict(recorded.entry.details)["reason"]
        for recorded in store.entries("REQ-5")
        if recorded.entry.kind is EventKind.OPERATION_FAILED
    ]
    assert reasons == ["account ACC-200 is frozen"]


def test_the_log_is_append_only(store) -> None:
    intake(store, make_request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00"))

    import sqlite3

    connection = sqlite3.connect(store.path)
    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        connection.execute("DELETE FROM events")
    connection.close()

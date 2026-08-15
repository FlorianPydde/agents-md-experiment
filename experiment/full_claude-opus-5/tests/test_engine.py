from __future__ import annotations

import pytest
from conftest import make_request

from app.engine import Runner
from app.enums import (
    Decision,
    EventKind,
    OperationName,
    RequestKind,
    RequestState,
    Role,
    StepState,
)
from app.errors import AppError
from app.store import Store
from app.values import Money


def test_small_credit_completes_without_approval(runner: Runner) -> None:
    record = runner.intake(
        make_request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00")
    )
    assert record.state is RequestState.COMPLETED
    assert record.approvals == ()
    assert runner.world.get("ACC-100").balance == Money.parse("1250.00")


def test_large_credit_stops_for_finance(runner: Runner) -> None:
    record = runner.intake(
        make_request("REQ-2", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00")
    )
    assert record.state is RequestState.AWAITING_APPROVAL
    pending = record.pending_approval()
    assert pending is not None
    assert pending.required_role is Role.FINANCE
    assert pending.operation is OperationName.APPLY_CREDIT
    assert runner.world.get("ACC-100").balance == Money.parse("1200.00")


def test_approval_resumes_the_run(runner: Runner) -> None:
    runner.intake(make_request("REQ-2", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))
    record = runner.resolve("REQ-2", Role.FINANCE, Decision.APPROVE)
    assert record.state is RequestState.COMPLETED
    assert all(s.state is StepState.DONE for s in record.steps)
    assert runner.world.get("ACC-100").balance == Money.parse("1450.00")


def test_rejection_stops_the_run(runner: Runner) -> None:
    runner.intake(
        make_request("REQ-3", RequestKind.ACCOUNT_RECOVERY, "ACC-200", "75.00")
    )
    record = runner.resolve("REQ-3", Role.RISK, Decision.REJECT)
    assert record.state is RequestState.REJECTED
    assert record.steps[1].state is StepState.SKIPPED
    assert record.steps[2].state is StepState.PENDING
    assert runner.world.get("ACC-200").frozen is True


def test_recovery_stops_twice_then_completes(runner: Runner) -> None:
    runner.intake(
        make_request("REQ-4", RequestKind.ACCOUNT_RECOVERY, "ACC-200", "250.00")
    )
    after_unfreeze = runner.resolve("REQ-4", Role.RISK, Decision.APPROVE)
    assert after_unfreeze.state is RequestState.AWAITING_APPROVAL
    second = after_unfreeze.pending_approval()
    assert second is not None
    assert second.operation is OperationName.APPLY_CREDIT
    final = runner.resolve("REQ-4", Role.FINANCE, Decision.APPROVE)
    assert final.state is RequestState.COMPLETED
    account = runner.world.get("ACC-200")
    assert account.frozen is False
    assert account.balance == Money.parse("300.00")


def test_wrong_role_is_refused(runner: Runner) -> None:
    runner.intake(make_request("REQ-5", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))
    with pytest.raises(AppError, match="needs finance"):
        runner.resolve("REQ-5", Role.RISK, Decision.APPROVE)


def test_decision_with_nothing_pending_is_refused(runner: Runner) -> None:
    runner.intake(make_request("REQ-6", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00"))
    with pytest.raises(AppError, match="nothing pending"):
        runner.resolve("REQ-6", Role.FINANCE, Decision.APPROVE)


def test_unknown_reference_is_refused(runner: Runner) -> None:
    with pytest.raises(AppError, match="no such request"):
        runner.resolve("REQ-NOPE", Role.FINANCE, Decision.APPROVE)


def test_debit_fails_on_frozen_account(runner: Runner, store: Store) -> None:
    runner.intake(make_request("REQ-7", RequestKind.COLLECT_DEBT, "ACC-200", "10.00"))
    record = runner.resolve("REQ-7", Role.FINANCE, Decision.APPROVE)
    assert record.state is RequestState.FAILED
    assert record.steps[1].state is StepState.FAILED
    kinds = [e.kind for e in store.log_for("REQ-7")]
    assert EventKind.OPERATION_FAILED in kinds
    assert runner.world.get("ACC-200").balance == Money.parse("50.00")


def test_debit_fails_when_balance_is_below_amount(runner: Runner) -> None:
    runner.intake(
        make_request("REQ-8", RequestKind.ACCOUNT_RECOVERY, "ACC-200", "250.00")
    )
    runner.resolve("REQ-8", Role.RISK, Decision.APPROVE)
    runner.resolve("REQ-8", Role.FINANCE, Decision.APPROVE)  # balance now 300.00
    runner.intake(make_request("REQ-9", RequestKind.COLLECT_DEBT, "ACC-200", "500.00"))
    record = runner.resolve("REQ-9", Role.FINANCE, Decision.APPROVE)
    assert record.state is RequestState.FAILED
    assert runner.world.get("ACC-200").balance == Money.parse("300.00")


def test_log_records_every_occurrence(runner: Runner, store: Store) -> None:
    runner.intake(make_request("REQ-A", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))
    runner.resolve("REQ-A", Role.FINANCE, Decision.APPROVE)
    kinds = {e.kind for e in store.log_for("REQ-A")}
    assert kinds == {
        EventKind.REQUEST_RECEIVED,
        EventKind.PLAN_CREATED,
        EventKind.POLICY_DECIDED,
        EventKind.APPROVAL_REQUESTED,
        EventKind.APPROVAL_RESOLVED,
        EventKind.OPERATION_RAN,
        EventKind.REQUEST_FINALISED,
    }


def test_repeated_lookup_does_not_repeat_the_work(runner: Runner, store: Store) -> None:
    runner.intake(make_request("REQ-B", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00"))
    runner.intake(make_request("REQ-C", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00"))
    before = store.reads
    for _ in range(5):
        store.load_request("REQ-B")
        store.load_request("REQ-C")
    assert store.reads - before <= 2

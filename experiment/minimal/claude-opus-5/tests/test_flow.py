from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain import (
    ApprovalState,
    Decision,
    DecisionEntry,
    EventType,
    Origin,
    Requester,
    RequestKind,
    RequestState,
    Role,
    ServiceRequest,
    StepState,
)
from app.engine import Engine
from app.errors import UsageError

INTERNAL = Requester(name="Nia Patel", role="agent", origin=Origin.INTERNAL)
EXTERNAL = Requester(name="Omar Haddad", role="agent", origin=Origin.EXTERNAL)


def request(
    reference: str, kind: RequestKind, account: str, amount: str, requester: Requester = INTERNAL
) -> ServiceRequest:
    return ServiceRequest(reference, kind, account, Decimal(amount), requester)


def test_small_credit_completes_without_approval(engine: Engine) -> None:
    record = engine.intake(
        request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00")
    )

    assert record.state is RequestState.COMPLETED
    assert all(step.state is StepState.DONE for step in record.steps)
    assert engine.ledger.get("ACC-100").balance == Decimal("1250.00")
    assert engine.store.pending_approvals() == ()


def test_large_credit_waits_for_finance_then_continues(engine: Engine) -> None:
    record = engine.intake(
        request("REQ-2", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00")
    )

    assert record.state is RequestState.AWAITING_APPROVAL
    pending = engine.store.pending_approval("REQ-2")
    assert pending is not None and pending.role is Role.FINANCE
    assert engine.ledger.get("ACC-100").balance == Decimal("1200.00")

    resumed = engine.decide(DecisionEntry("REQ-2", Role.FINANCE, Decision.APPROVE))

    assert resumed.state is RequestState.COMPLETED
    assert engine.ledger.get("ACC-100").balance == Decimal("1450.00")
    assert engine.store.pending_approvals() == ()


def test_rejection_stops_the_run(engine: Engine) -> None:
    engine.intake(
        request("REQ-3", RequestKind.ACCOUNT_RECOVERY, "ACC-200", "75.00", EXTERNAL)
    )

    record = engine.decide(DecisionEntry("REQ-3", Role.RISK, Decision.REJECT))

    assert record.state is RequestState.REJECTED
    assert engine.ledger.get("ACC-200").frozen is True
    assert engine.ledger.get("ACC-200").balance == Decimal("50.00")
    approvals = engine.store.approvals_for("REQ-3")
    assert [approval.state for approval in approvals] == [ApprovalState.REJECTED]


def test_a_failing_operation_fails_the_request(engine: Engine) -> None:
    engine.intake(request("REQ-4", RequestKind.COLLECT_DEBT, "ACC-200", "500.00", EXTERNAL))

    record = engine.decide(DecisionEntry("REQ-4", Role.FINANCE, Decision.APPROVE))

    assert record.state is RequestState.FAILED
    assert record.steps[1].state is StepState.FAILED
    assert engine.ledger.get("ACC-200").balance == Decimal("50.00")
    types = [event.type for event in engine.store.events_for("REQ-4")]
    assert EventType.OPERATION_FAILED in types
    assert types[-1] is EventType.REQUEST_FINISHED


def test_debit_below_balance_fails_on_an_unfrozen_account(engine: Engine) -> None:
    engine.intake(request("REQ-5", RequestKind.COLLECT_DEBT, "ACC-100", "5000.00"))

    record = engine.decide(DecisionEntry("REQ-5", Role.FINANCE, Decision.APPROVE))

    assert record.state is RequestState.FAILED
    assert engine.ledger.get("ACC-100").balance == Decimal("1200.00")


def test_recovery_unfreezes_then_credits_after_two_approvals(engine: Engine) -> None:
    engine.intake(
        request("REQ-6", RequestKind.ACCOUNT_RECOVERY, "ACC-200", "250.00", EXTERNAL)
    )

    after_risk = engine.decide(DecisionEntry("REQ-6", Role.RISK, Decision.APPROVE))
    assert after_risk.state is RequestState.AWAITING_APPROVAL
    assert engine.ledger.get("ACC-200").frozen is False

    after_finance = engine.decide(DecisionEntry("REQ-6", Role.FINANCE, Decision.APPROVE))
    assert after_finance.state is RequestState.COMPLETED
    assert engine.ledger.get("ACC-200").balance == Decimal("300.00")


def test_a_decision_from_the_wrong_role_is_refused(engine: Engine) -> None:
    engine.intake(request("REQ-7", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))

    with pytest.raises(UsageError, match="needs finance"):
        engine.decide(DecisionEntry("REQ-7", Role.RISK, Decision.APPROVE))


def test_a_decision_with_nothing_pending_is_refused(engine: Engine) -> None:
    engine.intake(request("REQ-8", RequestKind.GOODWILL_CREDIT, "ACC-100", "10.00"))

    with pytest.raises(UsageError, match="nothing pending"):
        engine.decide(DecisionEntry("REQ-8", Role.FINANCE, Decision.APPROVE))


def test_a_decision_for_an_unknown_request_is_refused(engine: Engine) -> None:
    with pytest.raises(UsageError, match="no request named REQ-404"):
        engine.decide(DecisionEntry("REQ-404", Role.FINANCE, Decision.APPROVE))


def test_the_same_reference_cannot_arrive_twice(engine: Engine) -> None:
    engine.intake(request("REQ-9", RequestKind.GOODWILL_CREDIT, "ACC-100", "10.00"))

    with pytest.raises(UsageError, match="already been received"):
        engine.intake(request("REQ-9", RequestKind.GOODWILL_CREDIT, "ACC-100", "10.00"))

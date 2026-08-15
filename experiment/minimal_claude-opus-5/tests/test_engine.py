from decimal import Decimal
from pathlib import Path

import pytest

from app.domain import (
    Account,
    ApprovalState,
    Decision,
    DecisionEntry,
    EventKind,
    RequestKind,
    RequestState,
    Requester,
    Role,
    ServiceRequest,
    StepState,
    Tier,
)
from app.engine import Engine
from app.errors import AppError
from app.store import Store
from app.world import World

ACC_OPEN = Account("ACC-100", "Ada", Tier.STANDARD, Decimal("1200.00"), False)
ACC_FROZEN = Account("ACC-200", "Grace", Tier.PREMIUM, Decimal("50.00"), True)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    store = Store(tmp_path / "test.db", reset=True)
    world = World((ACC_OPEN, ACC_FROZEN))
    store.save_accounts(world.sorted_accounts())
    return Engine(store, world)


def request_of(
    reference: str,
    kind: RequestKind,
    account: str,
    amount: str,
    origin_internal: bool = True,
) -> ServiceRequest:
    from app.domain import Origin

    return ServiceRequest(
        reference=reference,
        kind=kind,
        account=account,
        amount=Decimal(amount),
        requester=Requester(
            "Nia", "agent", Origin.INTERNAL if origin_internal else Origin.EXTERNAL
        ),
    )


def test_small_credit_completes_without_approval(engine: Engine) -> None:
    engine.intake(request_of("R-1", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00"))
    view = engine.store.view_request("R-1")
    assert view.request.state is RequestState.COMPLETED
    assert [s.state for s in view.steps] == [StepState.DONE] * 3
    assert view.approvals == ()
    assert engine.world.get("ACC-100").balance == Decimal("1250.00")


def test_large_credit_waits_then_completes_on_approval(engine: Engine) -> None:
    engine.intake(request_of("R-2", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))
    view = engine.store.view_request("R-2")
    assert view.request.state is RequestState.AWAITING_APPROVAL
    assert view.approvals[0].required_role is Role.FINANCE
    assert engine.world.get("ACC-100").balance == Decimal("1200.00")

    engine.decide(DecisionEntry("R-2", Role.FINANCE, Decision.APPROVE))
    view = engine.store.view_request("R-2")
    assert view.request.state is RequestState.COMPLETED
    assert view.approvals[0].state is ApprovalState.APPROVED
    assert engine.world.get("ACC-100").balance == Decimal("1450.00")


def test_rejection_stops_the_run(engine: Engine) -> None:
    engine.intake(request_of("R-3", RequestKind.ACCOUNT_RECOVERY, "ACC-200", "75.00"))
    engine.decide(DecisionEntry("R-3", Role.RISK, Decision.REJECT))
    view = engine.store.view_request("R-3")
    assert view.request.state is RequestState.REJECTED
    assert view.steps[1].state is StepState.REJECTED
    assert view.steps[2].state is StepState.PENDING
    assert engine.world.get("ACC-200").frozen is True


def test_debit_on_frozen_account_fails(engine: Engine) -> None:
    engine.intake(request_of("R-4", RequestKind.COLLECT_DEBT, "ACC-200", "500.00"))
    engine.decide(DecisionEntry("R-4", Role.FINANCE, Decision.APPROVE))
    view = engine.store.view_request("R-4")
    assert view.request.state is RequestState.FAILED
    assert view.steps[1].state is StepState.FAILED
    assert any(e.kind is EventKind.OPERATION_FAILED for e in view.log)
    assert engine.world.get("ACC-200").balance == Decimal("50.00")


def test_insufficient_balance_fails(engine: Engine) -> None:
    engine.world.set_frozen("ACC-200", False)
    engine.intake(request_of("R-5", RequestKind.COLLECT_DEBT, "ACC-200", "500.00"))
    engine.decide(DecisionEntry("R-5", Role.FINANCE, Decision.APPROVE))
    view = engine.store.view_request("R-5")
    assert view.request.state is RequestState.FAILED
    failure = [e for e in view.log if e.kind is EventKind.OPERATION_FAILED][0]
    assert "below the amount" in dict(failure.detail)["reason"]


def test_recovery_unfreezes_then_credits(engine: Engine) -> None:
    engine.intake(request_of("R-6", RequestKind.ACCOUNT_RECOVERY, "ACC-200", "75.00"))
    engine.decide(DecisionEntry("R-6", Role.RISK, Decision.APPROVE))
    assert engine.store.view_request("R-6").request.state is RequestState.COMPLETED
    assert engine.world.get("ACC-200").frozen is False
    assert engine.world.get("ACC-200").balance == Decimal("125.00")


def test_wrong_role_is_refused(engine: Engine) -> None:
    engine.intake(request_of("R-7", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))
    with pytest.raises(AppError, match="requires role 'finance'"):
        engine.decide(DecisionEntry("R-7", Role.RISK, Decision.APPROVE))


def test_decision_with_nothing_pending_is_refused(engine: Engine) -> None:
    engine.intake(request_of("R-8", RequestKind.GOODWILL_CREDIT, "ACC-100", "10.00"))
    with pytest.raises(AppError, match="nothing pending"):
        engine.decide(DecisionEntry("R-8", Role.FINANCE, Decision.APPROVE))


def test_decision_for_unknown_request_is_refused(engine: Engine) -> None:
    with pytest.raises(AppError, match="no such request: R-nope"):
        engine.decide(DecisionEntry("R-nope", Role.FINANCE, Decision.APPROVE))


def test_log_is_append_only_and_records_each_occurrence(engine: Engine) -> None:
    engine.intake(request_of("R-9", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))
    before = engine.store.log_of("R-9")
    engine.decide(DecisionEntry("R-9", Role.FINANCE, Decision.APPROVE))
    after = engine.store.log_of("R-9")
    assert after[: len(before)] == before
    kinds = [e.kind for e in after]
    assert EventKind.REQUEST_RECEIVED in kinds
    assert EventKind.PLAN_CREATED in kinds
    assert EventKind.POLICY_DECIDED in kinds
    assert EventKind.APPROVAL_REQUESTED in kinds
    assert EventKind.APPROVAL_RESOLVED in kinds
    assert EventKind.OPERATION_RAN in kinds
    assert EventKind.REQUEST_FINALISED in kinds


def test_repeated_lookup_is_served_from_cache(engine: Engine) -> None:
    engine.intake(request_of("R-10", RequestKind.GOODWILL_CREDIT, "ACC-100", "10.00"))
    engine.store.lookups = 0
    engine.store.view_request("R-10")
    engine.store.view_request("R-10")
    assert engine.store.lookups == 1

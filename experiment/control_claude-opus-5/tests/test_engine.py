from decimal import Decimal
from pathlib import Path

import pytest

from app.engine import Engine, replay
from app.errors import DecisionError, InputError, NotFoundError
from app.models import Account, ApprovalState, Decision, Origin, Role, State, StepState, Tier
from app.store import Store
from app.world import World


def make_engine(tmp_path: Path, accounts=None) -> Engine:
    accounts = accounts or [
        Account("ACC-100", "Ada Lovelace", Tier.STANDARD, Decimal("1200.00"), False),
        Account("ACC-200", "Grace Hopper", Tier.PREMIUM, Decimal("50.00"), True),
    ]
    store = Store.fresh(tmp_path / "test.db")
    return Engine(World(accounts), store)


def intake(engine: Engine, reference: str, kind: str, account: str, amount: str, origin="internal"):
    return engine.intake(
        {
            "reference": reference,
            "kind": kind,
            "account": account,
            "amount": amount,
            "requester": {"name": "Nia Patel", "role": "agent", "origin": origin},
        },
        "test.request",
    )


def test_small_credit_completes_without_approval(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    request = intake(engine, "REQ-1", "goodwill_credit", "ACC-100", "50.00")
    assert request.state is State.COMPLETED
    assert request.approvals == []
    assert engine.world.account("ACC-100").balance == Decimal("1250.00")


def test_large_credit_stops_for_finance_then_continues(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    request = intake(engine, "REQ-2", "goodwill_credit", "ACC-100", "250.00")

    assert request.state is State.AWAITING_APPROVAL
    pending = request.pending_approval
    assert pending is not None
    assert pending.required_role is Role.FINANCE
    assert pending.operation == "apply_credit"
    assert request.steps[0].state is StepState.DONE
    assert request.steps[1].state is StepState.AWAITING_APPROVAL
    assert engine.world.account("ACC-100").balance == Decimal("1200.00")

    engine.decide("REQ-2", Role.FINANCE, Decision.APPROVE)
    assert request.state is State.COMPLETED
    assert request.approvals[0].state is ApprovalState.APPROVED
    assert engine.world.account("ACC-100").balance == Decimal("1450.00")


def test_reject_stops_the_run(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    request = intake(engine, "REQ-3", "account_recovery", "ACC-200", "75.00", origin="external")

    assert request.state is State.AWAITING_APPROVAL
    assert request.pending_approval.required_role is Role.RISK

    engine.decide("REQ-3", Role.RISK, Decision.REJECT)
    assert request.state is State.REJECTED
    assert engine.world.account("ACC-200").frozen is True
    assert engine.world.account("ACC-200").balance == Decimal("50.00")
    assert all(step.state is StepState.SKIPPED for step in request.steps[1:])


def test_recovery_can_stop_twice(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    request = intake(engine, "REQ-4", "account_recovery", "ACC-200", "500.00", origin="external")

    assert request.pending_approval.required_role is Role.RISK
    engine.decide("REQ-4", Role.RISK, Decision.APPROVE)

    assert request.state is State.AWAITING_APPROVAL
    assert request.pending_approval.required_role is Role.FINANCE
    assert request.pending_approval.operation == "apply_credit"
    assert engine.world.account("ACC-200").frozen is False

    engine.decide("REQ-4", Role.FINANCE, Decision.APPROVE)
    assert request.state is State.COMPLETED
    assert engine.world.account("ACC-200").balance == Decimal("550.00")


def test_debit_beyond_balance_fails_the_request(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    request = intake(engine, "REQ-5", "collect_debt", "ACC-100", "5000.00")
    engine.decide("REQ-5", Role.FINANCE, Decision.APPROVE)

    assert request.state is State.FAILED
    assert request.steps[1].state is StepState.FAILED
    assert "below the debit amount" in request.steps[1].detail
    assert request.steps[2].state is StepState.SKIPPED
    assert engine.world.account("ACC-100").balance == Decimal("1200.00")
    assert any(entry.event == "operation_failed" for entry in engine.store.log_entries("REQ-5"))


def test_write_on_a_frozen_account_fails(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    request = intake(engine, "REQ-6", "goodwill_credit", "ACC-200", "10.00")
    assert request.state is State.FAILED
    assert "frozen" in request.steps[1].detail


def test_decision_from_the_wrong_role_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    intake(engine, "REQ-7", "goodwill_credit", "ACC-100", "250.00")
    with pytest.raises(DecisionError, match="requires role 'finance'"):
        engine.decide("REQ-7", Role.RISK, Decision.APPROVE)


def test_decision_without_anything_pending_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    intake(engine, "REQ-8", "goodwill_credit", "ACC-100", "10.00")
    with pytest.raises(DecisionError, match="nothing pending"):
        engine.decide("REQ-8", Role.FINANCE, Decision.APPROVE)


def test_decision_for_an_unknown_reference_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with pytest.raises(NotFoundError):
        engine.decide("REQ-NOPE", Role.FINANCE, Decision.APPROVE)


def test_negative_amount_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with pytest.raises(InputError, match="must not be negative"):
        intake(engine, "REQ-9", "goodwill_credit", "ACC-100", "-5.00")


def test_missing_field_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with pytest.raises(InputError, match="missing the required field 'amount'"):
        engine.intake(
            {
                "reference": "REQ-10",
                "kind": "goodwill_credit",
                "account": "ACC-100",
                "requester": {"name": "n", "role": "agent", "origin": "internal"},
            },
            "test.request",
        )


def test_unknown_kind_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with pytest.raises(InputError, match="must be one of"):
        intake(engine, "REQ-11", "make_coffee", "ACC-100", "1.00")


def test_unknown_scenario_action_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with pytest.raises(InputError, match="must be one of: intake, decide"):
        replay(engine, {"steps": [{"action": "dance"}]})


def test_notification_template_follows_origin(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    internal = intake(engine, "REQ-12", "goodwill_credit", "ACC-100", "10.00")
    external = intake(engine, "REQ-13", "goodwill_credit", "ACC-100", "10.00", origin="external")
    assert "Internal notice" in internal.steps[2].detail
    assert "Dear Ada Lovelace" in external.steps[2].detail


def test_log_is_append_only(tmp_path: Path) -> None:
    import sqlite3

    engine = make_engine(tmp_path)
    intake(engine, "REQ-14", "goodwill_credit", "ACC-100", "10.00")
    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        engine.store._conn.execute("DELETE FROM log")
    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        engine.store._conn.execute("UPDATE log SET event = 'tampered'")


def test_log_records_every_kind_of_occurrence(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    intake(engine, "REQ-15", "goodwill_credit", "ACC-100", "250.00")
    engine.decide("REQ-15", Role.FINANCE, Decision.APPROVE)
    events = [entry.event for entry in engine.store.log_entries("REQ-15")]
    for expected in (
        "request_received",
        "plan_created",
        "policy_evaluated",
        "approval_requested",
        "approval_resolved",
        "operation_ran",
        "request_finished",
    ):
        assert expected in events


def test_state_survives_between_runs(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    engine = make_engine(tmp_path)
    intake(engine, "REQ-16", "goodwill_credit", "ACC-100", "250.00")
    engine.store.close()

    from app.api import load_engine

    reloaded = load_engine(db)
    request = reloaded.request("REQ-16")
    assert request.state is State.AWAITING_APPROVAL
    assert request.pending_approval.required_role is Role.FINANCE
    reloaded.decide("REQ-16", Role.FINANCE, Decision.APPROVE)
    assert reloaded.request("REQ-16").state is State.COMPLETED
    assert reloaded.world.account("ACC-100").balance == Decimal("1450.00")
    reloaded.store.close()

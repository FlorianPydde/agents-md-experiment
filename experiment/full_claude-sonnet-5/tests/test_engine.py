import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.db import Database
from app.engine import Engine
from app.errors import AppError
from app.loader import load_scenario, load_world
from app.operations import Account, World


REPO_DIR = Path(__file__).resolve().parent.parent


def make_world():
    world = World()
    world.accounts["ACC-100"] = Account(
        id="ACC-100", owner="Ada Lovelace", tier="standard", balance=Decimal("1200.00"), frozen=False
    )
    world.accounts["ACC-200"] = Account(
        id="ACC-200", owner="Grace Hopper", tier="premium", balance=Decimal("50.00"), frozen=True
    )
    return world


def make_engine(tmp_path, world=None):
    db = Database(tmp_path / "test.db")
    db.reset()
    world = world or make_world()
    for account in world.accounts.values():
        db.upsert_account(account.id, account.owner, account.tier, account.balance, account.frozen)
    db.commit()
    return Engine(db, world), db


def test_acceptance_output_matches_exactly(tmp_path):
    world_path = REPO_DIR / "world.json"
    scenario_path = REPO_DIR / "scenario.json"

    world = load_world(str(world_path))
    scenario = load_scenario(str(scenario_path))

    db = Database(tmp_path / "acceptance.db")
    db.reset()
    for account in world.accounts.values():
        db.upsert_account(account.id, account.owner, account.tier, account.balance, account.frozen)
    db.commit()

    engine = Engine(db, world)
    for entry in scenario:
        if entry["action"] == "intake":
            engine.intake(entry["request"])
        else:
            engine.decide(entry["reference"], entry["role"], entry["decision"])
        db.commit()

    lines = []
    for row in db.all_requests_in_arrival_order():
        lines.append(f"{row['reference']:<10}{row['kind']:<20}{row['state']}")
    for row in db.all_accounts():
        balance = Decimal(row["balance"])
        status = "frozen" if row["frozen"] else "active"
        lines.append(f"{row['id']:<10}{balance:>10.2f}  {status}")

    expected = (
        "REQ-1001  goodwill_credit     completed\n"
        "REQ-1002  goodwill_credit     completed\n"
        "REQ-1003  account_recovery    rejected\n"
        "REQ-1004  collect_debt        failed\n"
        "ACC-100      1500.00  active\n"
        "ACC-200        50.00  frozen"
    )
    assert "\n".join(lines) == expected
    db.close()


# -- policy rule tests --------------------------------------------------------


def test_read_operation_runs_without_approval(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.intake(
        {
            "reference": "R1",
            "kind": "goodwill_credit",
            "account": "ACC-100",
            "amount": "10.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    row = db.get_request("R1")
    # small goodwill credit auto-runs entirely (rule 1 for read, rule 2 for credit)
    assert row["state"] == "completed"


def test_apply_credit_at_limit_runs_without_approval(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.intake(
        {
            "reference": "R2",
            "kind": "goodwill_credit",
            "account": "ACC-100",
            "amount": "100.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    assert db.get_request("R2")["state"] == "completed"


def test_apply_credit_above_limit_needs_finance(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.intake(
        {
            "reference": "R3",
            "kind": "goodwill_credit",
            "account": "ACC-100",
            "amount": "100.01",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    assert db.get_request("R3")["state"] == "awaiting_approval"
    pending = db.pending_approval_for("R3")
    assert pending["required_role"] == "finance"


def test_apply_debit_always_needs_finance(tmp_path):
    engine, db = make_engine(tmp_path)
    world = db  # noqa
    engine.world.accounts["ACC-100"].frozen = False
    engine.intake(
        {
            "reference": "R4",
            "kind": "collect_debt",
            "account": "ACC-100",
            "amount": "5.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    assert db.get_request("R4")["state"] == "awaiting_approval"
    pending = db.pending_approval_for("R4")
    assert pending["required_role"] == "finance"


def test_unfreeze_needs_risk(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.intake(
        {
            "reference": "R5",
            "kind": "account_recovery",
            "account": "ACC-200",
            "amount": "10.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    assert db.get_request("R5")["state"] == "awaiting_approval"
    pending = db.pending_approval_for("R5")
    assert pending["required_role"] == "risk"


# -- approval flow tests -------------------------------------------------------


def test_approve_continues_and_completes(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.intake(
        {
            "reference": "R6",
            "kind": "goodwill_credit",
            "account": "ACC-100",
            "amount": "200.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    assert db.get_request("R6")["state"] == "awaiting_approval"
    engine.decide("R6", "finance", "approve")
    row = db.get_request("R6")
    assert row["state"] == "completed"
    account = db.get_account("ACC-100")
    assert Decimal(account["balance"]) == Decimal("1400.00")


def test_reject_stops_request_permanently(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.intake(
        {
            "reference": "R7",
            "kind": "goodwill_credit",
            "account": "ACC-100",
            "amount": "200.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    engine.decide("R7", "finance", "reject")
    row = db.get_request("R7")
    assert row["state"] == "rejected"
    account = db.get_account("ACC-100")
    assert Decimal(account["balance"]) == Decimal("1200.00")  # unchanged


def test_decide_with_wrong_role_is_rejected(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.intake(
        {
            "reference": "R8",
            "kind": "goodwill_credit",
            "account": "ACC-100",
            "amount": "200.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    with pytest.raises(AppError):
        engine.decide("R8", "risk", "approve")


def test_decide_with_nothing_pending_is_rejected(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.intake(
        {
            "reference": "R9",
            "kind": "goodwill_credit",
            "account": "ACC-100",
            "amount": "10.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    # R9 auto-completes; no pending approval
    with pytest.raises(AppError):
        engine.decide("R9", "finance", "approve")


def test_decide_unknown_reference_is_rejected(tmp_path):
    engine, db = make_engine(tmp_path)
    with pytest.raises(AppError):
        engine.decide("NOPE", "finance", "approve")


# -- failing operation tests ---------------------------------------------------


def test_apply_debit_fails_when_insufficient_balance(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.world.accounts["ACC-100"].balance = Decimal("10.00")
    engine.intake(
        {
            "reference": "R10",
            "kind": "collect_debt",
            "account": "ACC-100",
            "amount": "500.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    engine.decide("R10", "finance", "approve")
    row = db.get_request("R10")
    assert row["state"] == "failed"
    # balance unchanged since debit failed
    account = db.get_account("ACC-100")
    assert Decimal(account["balance"]) == Decimal("10.00")


def test_write_operation_fails_on_frozen_account(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.intake(
        {
            "reference": "R11",
            "kind": "goodwill_credit",
            "account": "ACC-200",  # frozen
            "amount": "10.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    row = db.get_request("R11")
    assert row["state"] == "failed"


# -- validation / error tests ---------------------------------------------------


def test_negative_amount_rejected(tmp_path):
    engine, db = make_engine(tmp_path)
    with pytest.raises(AppError):
        engine.intake(
            {
                "reference": "R12",
                "kind": "goodwill_credit",
                "account": "ACC-100",
                "amount": "-5.00",
                "requester": {"name": "A", "role": "agent", "origin": "internal"},
            }
        )


def test_unknown_kind_rejected(tmp_path):
    engine, db = make_engine(tmp_path)
    with pytest.raises(AppError):
        engine.intake(
            {
                "reference": "R13",
                "kind": "bogus_kind",
                "account": "ACC-100",
                "amount": "5.00",
                "requester": {"name": "A", "role": "agent", "origin": "internal"},
            }
        )


def test_unknown_account_rejected(tmp_path):
    engine, db = make_engine(tmp_path)
    with pytest.raises(AppError):
        engine.intake(
            {
                "reference": "R14",
                "kind": "goodwill_credit",
                "account": "ACC-999",
                "amount": "5.00",
                "requester": {"name": "A", "role": "agent", "origin": "internal"},
            }
        )


def test_missing_field_rejected(tmp_path):
    engine, db = make_engine(tmp_path)
    with pytest.raises(AppError):
        engine.intake(
            {
                "reference": "R15",
                "kind": "goodwill_credit",
                "account": "ACC-100",
                "requester": {"name": "A", "role": "agent", "origin": "internal"},
            }
        )

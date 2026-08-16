"""End to end tests for the engine: approval flow and failures."""

from decimal import Decimal

import pytest

from app.engine import Engine
from app.errors import AppError
from app.models import load_scenario, load_world
from app.storage import Storage

WORLD = {
    "accounts": [
        {"id": "ACC-100", "owner": "Ada Lovelace", "tier": "standard", "balance": "1200.00", "frozen": False},
        {"id": "ACC-200", "owner": "Grace Hopper", "tier": "premium", "balance": "50.00", "frozen": True},
    ]
}


def build_engine(tmp_path, world=WORLD):
    storage = Storage.create_fresh(tmp_path / "log.db")
    engine = Engine(storage)
    engine.load_world(load_world(world))
    return engine, storage


def test_acceptance_scenario_matches_expected_states(tmp_path):
    scenario = {
        "steps": [
            {
                "action": "intake",
                "request": {
                    "reference": "REQ-1001",
                    "kind": "goodwill_credit",
                    "account": "ACC-100",
                    "amount": "50.00",
                    "requester": {"name": "Nia Patel", "role": "agent", "origin": "internal"},
                },
            },
            {
                "action": "intake",
                "request": {
                    "reference": "REQ-1002",
                    "kind": "goodwill_credit",
                    "account": "ACC-100",
                    "amount": "250.00",
                    "requester": {"name": "Nia Patel", "role": "agent", "origin": "internal"},
                },
            },
            {"action": "decide", "reference": "REQ-1002", "role": "finance", "decision": "approve"},
            {
                "action": "intake",
                "request": {
                    "reference": "REQ-1003",
                    "kind": "account_recovery",
                    "account": "ACC-200",
                    "amount": "75.00",
                    "requester": {"name": "Omar Haddad", "role": "agent", "origin": "external"},
                },
            },
            {"action": "decide", "reference": "REQ-1003", "role": "risk", "decision": "reject"},
            {
                "action": "intake",
                "request": {
                    "reference": "REQ-1004",
                    "kind": "collect_debt",
                    "account": "ACC-200",
                    "amount": "500.00",
                    "requester": {"name": "Omar Haddad", "role": "agent", "origin": "external"},
                },
            },
            {"action": "decide", "reference": "REQ-1004", "role": "finance", "decision": "approve"},
        ]
    }

    engine, storage = build_engine(tmp_path)
    for action, entry in load_scenario(scenario):
        if action == "intake":
            engine.intake(entry)
        else:
            engine.decide(entry)

    states = {row["reference"]: row["state"] for row in storage.all_requests_in_arrival_order()}
    assert states == {
        "REQ-1001": "completed",
        "REQ-1002": "completed",
        "REQ-1003": "rejected",
        "REQ-1004": "failed",
    }

    acc100 = storage.get_account("ACC-100")
    acc200 = storage.get_account("ACC-200")
    assert Decimal(acc100["balance"]) == Decimal("1500.00")
    assert bool(acc100["frozen"]) is False
    assert Decimal(acc200["balance"]) == Decimal("50.00")
    assert bool(acc200["frozen"]) is True


def test_low_value_credit_needs_no_approval_and_completes(tmp_path):
    engine, storage = build_engine(tmp_path)
    entries = load_scenario(
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "REQ-1",
                        "kind": "goodwill_credit",
                        "account": "ACC-100",
                        "amount": "10.00",
                        "requester": {"name": "A", "role": "agent", "origin": "internal"},
                    },
                }
            ]
        }
    )
    for action, entry in entries:
        engine.intake(entry)
    row = storage.get_request("REQ-1")
    assert row["state"] == "completed"
    approvals = storage.approvals_for_request("REQ-1")
    assert approvals == []


def test_high_value_credit_awaits_finance_approval(tmp_path):
    engine, storage = build_engine(tmp_path)
    entries = load_scenario(
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "REQ-1",
                        "kind": "goodwill_credit",
                        "account": "ACC-100",
                        "amount": "150.00",
                        "requester": {"name": "A", "role": "agent", "origin": "internal"},
                    },
                }
            ]
        }
    )
    action, entry = entries[0]
    engine.intake(entry)
    row = storage.get_request("REQ-1")
    assert row["state"] == "awaiting_approval"
    pending = storage.pending_approval_for_request("REQ-1")
    assert pending["role_required"] == "finance"


def test_approval_from_wrong_role_is_rejected_with_error(tmp_path):
    engine, storage = build_engine(tmp_path)
    entries = load_scenario(
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "REQ-1",
                        "kind": "goodwill_credit",
                        "account": "ACC-100",
                        "amount": "150.00",
                        "requester": {"name": "A", "role": "agent", "origin": "internal"},
                    },
                },
                {"action": "decide", "reference": "REQ-1", "role": "risk", "decision": "approve"},
            ]
        }
    )
    engine.intake(entries[0][1])
    with pytest.raises(AppError):
        engine.decide(entries[1][1])
    # The request is still awaiting approval; nothing was consumed.
    row = storage.get_request("REQ-1")
    assert row["state"] == "awaiting_approval"


def test_decide_with_nothing_pending_raises(tmp_path):
    engine, storage = build_engine(tmp_path)
    entries = load_scenario(
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "REQ-1",
                        "kind": "goodwill_credit",
                        "account": "ACC-100",
                        "amount": "10.00",
                        "requester": {"name": "A", "role": "agent", "origin": "internal"},
                    },
                },
                {"action": "decide", "reference": "REQ-1", "role": "finance", "decision": "approve"},
            ]
        }
    )
    engine.intake(entries[0][1])
    with pytest.raises(AppError):
        engine.decide(entries[1][1])


def test_debit_exceeding_balance_fails_the_request(tmp_path):
    engine, storage = build_engine(tmp_path)
    entries = load_scenario(
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "REQ-1",
                        "kind": "collect_debt",
                        "account": "ACC-100",
                        "amount": "5000.00",
                        "requester": {"name": "A", "role": "agent", "origin": "internal"},
                    },
                },
                {"action": "decide", "reference": "REQ-1", "role": "finance", "decision": "approve"},
            ]
        }
    )
    engine.intake(entries[0][1])
    engine.decide(entries[1][1])
    row = storage.get_request("REQ-1")
    assert row["state"] == "failed"
    acc100 = storage.get_account("ACC-100")
    assert Decimal(acc100["balance"]) == Decimal("1200.00")

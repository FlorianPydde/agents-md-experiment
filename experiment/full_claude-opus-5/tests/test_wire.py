"""Tests for parsing untrusted world and scenario data."""

import json

import pytest

from app.wire import WireError, load_scenario, load_world


def test_load_world_parses_accounts() -> None:
    raw = json.dumps(
        {
            "accounts": [
                {"id": "ACC-1", "owner": "Ada", "tier": "standard", "balance": "10.00", "frozen": False},
            ]
        }
    ).encode()

    accounts = load_world(raw)

    assert accounts["ACC-1"].owner == "Ada"
    assert accounts["ACC-1"].balance.formatted() == "10.00"


def test_load_world_rejects_malformed_json() -> None:
    with pytest.raises(WireError):
        load_world(b"not json")


def test_load_world_rejects_unknown_tier() -> None:
    raw = json.dumps(
        {"accounts": [{"id": "ACC-1", "owner": "Ada", "tier": "gold", "balance": "10.00", "frozen": False}]}
    ).encode()

    with pytest.raises(WireError):
        load_world(raw)


def test_load_world_rejects_negative_balance() -> None:
    raw = json.dumps(
        {"accounts": [{"id": "ACC-1", "owner": "Ada", "tier": "standard", "balance": "-10.00", "frozen": False}]}
    ).encode()

    with pytest.raises(WireError):
        load_world(raw)


def test_load_scenario_parses_intake_and_decide() -> None:
    raw = json.dumps(
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "REQ-1",
                        "kind": "goodwill_credit",
                        "account": "ACC-1",
                        "amount": "10.00",
                        "requester": {"name": "Ada", "role": "agent", "origin": "internal"},
                    },
                },
                {"action": "decide", "reference": "REQ-1", "role": "finance", "decision": "approve"},
            ]
        }
    ).encode()

    steps = load_scenario(raw)

    assert len(steps) == 2


def test_load_scenario_rejects_unknown_kind() -> None:
    raw = json.dumps(
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "REQ-1",
                        "kind": "not_a_real_kind",
                        "account": "ACC-1",
                        "amount": "10.00",
                        "requester": {"name": "Ada", "role": "agent", "origin": "internal"},
                    },
                }
            ]
        }
    ).encode()

    with pytest.raises(WireError):
        load_scenario(raw)


def test_load_scenario_rejects_unknown_action() -> None:
    raw = json.dumps({"steps": [{"action": "cancel", "reference": "REQ-1"}]}).encode()

    with pytest.raises(WireError):
        load_scenario(raw)

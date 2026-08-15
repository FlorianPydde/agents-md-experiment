"""Tests for input validation in loaders.py (SPEC: error handling)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.errors import ValidationError
from app.loaders import load_scenario, load_world


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_missing_world_file_raises(tmp_path):
    with pytest.raises(ValidationError):
        load_world(tmp_path / "does_not_exist.json")


def test_malformed_json_raises(tmp_path):
    path = tmp_path / "world.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_world(path)


def test_negative_balance_rejected(tmp_path):
    path = _write(
        tmp_path / "world.json",
        {
            "accounts": [
                {"id": "A", "owner": "O", "tier": "standard", "balance": "-1.00", "frozen": False}
            ]
        },
    )
    with pytest.raises(ValidationError):
        load_world(path)


def test_invalid_tier_rejected(tmp_path):
    path = _write(
        tmp_path / "world.json",
        {
            "accounts": [
                {"id": "A", "owner": "O", "tier": "gold", "balance": "1.00", "frozen": False}
            ]
        },
    )
    with pytest.raises(ValidationError):
        load_world(path)


def test_missing_required_field_rejected(tmp_path):
    path = _write(
        tmp_path / "world.json",
        {"accounts": [{"id": "A", "owner": "O", "tier": "standard", "frozen": False}]},
    )
    with pytest.raises(ValidationError):
        load_world(path)


def test_valid_world_loads(tmp_path):
    path = _write(
        tmp_path / "world.json",
        {
            "accounts": [
                {"id": "A", "owner": "O", "tier": "standard", "balance": "12.50", "frozen": False}
            ]
        },
    )
    accounts = load_world(path)
    assert len(accounts) == 1
    assert accounts[0].balance == Decimal("12.50")


def test_invalid_kind_rejected(tmp_path):
    path = _write(
        tmp_path / "scenario.json",
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "R1",
                        "kind": "not_a_kind",
                        "account": "A",
                        "amount": "1.00",
                        "requester": {"name": "N", "role": "agent", "origin": "internal"},
                    },
                }
            ]
        },
    )
    with pytest.raises(ValidationError):
        load_scenario(path)


def test_invalid_origin_rejected(tmp_path):
    path = _write(
        tmp_path / "scenario.json",
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "R1",
                        "kind": "goodwill_credit",
                        "account": "A",
                        "amount": "1.00",
                        "requester": {"name": "N", "role": "agent", "origin": "outer_space"},
                    },
                }
            ]
        },
    )
    with pytest.raises(ValidationError):
        load_scenario(path)


def test_invalid_decision_rejected(tmp_path):
    path = _write(
        tmp_path / "scenario.json",
        {"steps": [{"action": "decide", "reference": "R1", "role": "finance", "decision": "maybe"}]},
    )
    with pytest.raises(ValidationError):
        load_scenario(path)


def test_unknown_action_rejected(tmp_path):
    path = _write(tmp_path / "scenario.json", {"steps": [{"action": "teleport"}]})
    with pytest.raises(ValidationError):
        load_scenario(path)

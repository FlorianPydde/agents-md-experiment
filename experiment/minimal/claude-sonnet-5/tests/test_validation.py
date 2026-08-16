"""Tests for malformed input handling (SPEC.md "Errors" section)."""

from __future__ import annotations

import json

import pytest

from app.errors import AppError
from app.scenario import load_scenario
from app.world import load_world


def test_missing_world_file_is_an_error(tmp_path):
    with pytest.raises(AppError):
        load_world(tmp_path / "does-not-exist.json")


def test_malformed_world_json_is_an_error(tmp_path):
    path = tmp_path / "world.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(AppError):
        load_world(path)


def test_world_with_unknown_tier_is_an_error(tmp_path):
    path = tmp_path / "world.json"
    path.write_text(
        json.dumps(
            {"accounts": [{"id": "A", "owner": "O", "tier": "gold", "balance": "1.00", "frozen": False}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(AppError):
        load_world(path)


def test_world_with_negative_balance_is_an_error(tmp_path):
    path = tmp_path / "world.json"
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {"id": "A", "owner": "O", "tier": "standard", "balance": "-1.00", "frozen": False}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AppError):
        load_world(path)


def test_world_missing_required_field_is_an_error(tmp_path):
    path = tmp_path / "world.json"
    path.write_text(
        json.dumps({"accounts": [{"id": "A", "owner": "O", "tier": "standard", "balance": "1.00"}]}),
        encoding="utf-8",
    )
    with pytest.raises(AppError):
        load_world(path)


def test_scenario_with_unknown_kind_is_an_error(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
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
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AppError):
        load_scenario(path)


def test_scenario_with_negative_amount_is_an_error(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "action": "intake",
                        "request": {
                            "reference": "R1",
                            "kind": "goodwill_credit",
                            "account": "A",
                            "amount": "-1.00",
                            "requester": {"name": "N", "role": "agent", "origin": "internal"},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AppError):
        load_scenario(path)


def test_scenario_with_unknown_origin_is_an_error(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "action": "intake",
                        "request": {
                            "reference": "R1",
                            "kind": "goodwill_credit",
                            "account": "A",
                            "amount": "1.00",
                            "requester": {"name": "N", "role": "agent", "origin": "nowhere"},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AppError):
        load_scenario(path)


def test_scenario_missing_required_field_is_an_error(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps({"steps": [{"action": "intake", "request": {"reference": "R1"}}]}),
        encoding="utf-8",
    )
    with pytest.raises(AppError):
        load_scenario(path)


def test_scenario_with_unknown_decision_is_an_error(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps({"steps": [{"action": "decide", "reference": "R1", "role": "finance", "decision": "maybe"}]}),
        encoding="utf-8",
    )
    with pytest.raises(AppError):
        load_scenario(path)


def test_scenario_with_unknown_action_is_an_error(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({"steps": [{"action": "teleport"}]}), encoding="utf-8")
    with pytest.raises(AppError):
        load_scenario(path)

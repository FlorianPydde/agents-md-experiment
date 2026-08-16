from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain import Origin, RequestKind, ServiceRequest
from app.errors import InputError
from app.loading import load_scenario, load_world

FOLDER = Path(__file__).resolve().parent.parent


def test_the_supplied_world_loads() -> None:
    accounts = load_world(FOLDER / "world.json")

    assert [account.id for account in accounts] == ["ACC-100", "ACC-200"]
    assert accounts[1].frozen is True


def test_the_supplied_scenario_loads() -> None:
    scenario = load_scenario(FOLDER / "scenario.json")

    intakes = [entry for entry in scenario.entries if isinstance(entry, ServiceRequest)]
    assert len(scenario.entries) == 7
    assert intakes[0].kind is RequestKind.GOODWILL_CREDIT
    assert intakes[2].requester.origin is Origin.EXTERNAL


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="file not found"):
        load_world(tmp_path / "absent.json")


def test_malformed_json_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "world.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(InputError, match="not valid JSON"):
        load_world(path)


def write(tmp_path: Path, name: str, payload: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_an_unknown_tier_is_reported(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "world.json",
        {"accounts": [{"id": "A", "owner": "O", "tier": "gold", "balance": "1.00", "frozen": False}]},
    )

    with pytest.raises(InputError, match="must be one of: standard, premium"):
        load_world(path)


def test_a_missing_field_is_reported(tmp_path: Path) -> None:
    path = write(tmp_path, "world.json", {"accounts": [{"id": "A", "owner": "O", "tier": "standard"}]})

    with pytest.raises(InputError, match="missing the required field 'balance'"):
        load_world(path)


def test_a_negative_balance_is_reported(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "world.json",
        {"accounts": [{"id": "A", "owner": "O", "tier": "standard", "balance": "-1.00", "frozen": False}]},
    )

    with pytest.raises(InputError, match="must not be negative"):
        load_world(path)


def test_an_unknown_kind_is_reported(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "scenario.json",
        {
            "steps": [
                {
                    "action": "intake",
                    "request": {
                        "reference": "REQ-1",
                        "kind": "refund",
                        "account": "A",
                        "amount": "1.00",
                        "requester": {"name": "N", "role": "agent", "origin": "internal"},
                    },
                }
            ]
        },
    )

    with pytest.raises(InputError, match="must be one of"):
        load_scenario(path)


def test_an_unknown_action_is_reported(tmp_path: Path) -> None:
    path = write(tmp_path, "scenario.json", {"steps": [{"action": "cancel"}]})

    with pytest.raises(InputError, match="'action'"):
        load_scenario(path)


def test_an_unknown_deciding_role_is_reported(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "scenario.json",
        {"steps": [{"action": "decide", "reference": "REQ-1", "role": "intern", "decision": "approve"}]},
    )

    with pytest.raises(InputError, match="must be one of: finance, risk, supervisor"):
        load_scenario(path)

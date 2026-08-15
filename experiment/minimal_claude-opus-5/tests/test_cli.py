import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.cli import main
from app.errors import AppError
from app.parsing import parse_scenario, parse_world

ROOT = Path(__file__).resolve().parent.parent

ACCEPTANCE = (
    "REQ-1001  goodwill_credit     completed\n"
    "REQ-1002  goodwill_credit     completed\n"
    "REQ-1003  account_recovery    rejected\n"
    "REQ-1004  collect_debt        failed\n"
    "ACC-100      1500.00  active\n"
    "ACC-200        50.00  frozen\n"
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_run_matches_the_acceptance_output_exactly() -> None:
    result = run_cli("run", "scenario.json", "--world", "world.json")
    assert result.returncode == 0
    assert result.stdout.replace("\r\n", "\n") == ACCEPTANCE


def test_run_is_repeatable() -> None:
    first = run_cli("run", "scenario.json", "--world", "world.json").stdout
    second = run_cli("run", "scenario.json", "--world", "world.json").stdout
    assert first == second


def test_show_unknown_reference_exits_non_zero() -> None:
    run_cli("run", "scenario.json", "--world", "world.json")
    result = run_cli("show", "REQ-9999")
    assert result.returncode != 0
    assert "REQ-9999" in result.stderr
    assert "Traceback" not in result.stderr


def test_show_accepts_several_references() -> None:
    run_cli("run", "scenario.json", "--world", "world.json")
    result = run_cli("show", "REQ-1002", "REQ-1002", "REQ-1004")
    assert result.returncode == 0
    assert result.stdout.count("request   REQ-1002") == 2
    assert "request   REQ-1004" in result.stdout


def test_export_writes_each_format(tmp_path: Path) -> None:
    run_cli("run", "scenario.json", "--world", "world.json")
    for fmt in ("csv", "json", "tsv"):
        result = run_cli("export", "--format", fmt)
        assert result.returncode == 0
        path = ROOT / f"report.{fmt}"
        assert path.exists()
    payload = json.loads((ROOT / "report.json").read_text(encoding="utf-8"))
    assert [row["reference"] for row in payload] == [
        "REQ-1001",
        "REQ-1002",
        "REQ-1003",
        "REQ-1004",
    ]
    assert payload[3]["state"] == "failed"


def test_missing_file_is_reported_cleanly() -> None:
    result = run_cli("run", "nope.json", "--world", "world.json")
    assert result.returncode != 0
    assert "file not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_malformed_json_is_reported_cleanly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(AppError, match="malformed JSON"):
        parse_world(bad)


def test_unknown_enum_value_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "world.json"
    bad.write_text(
        json.dumps(
            {"accounts": [{"id": "A", "owner": "o", "tier": "gold",
                           "balance": "1.00", "frozen": False}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(AppError, match="expected one of: standard, premium"):
        parse_world(bad)


def test_negative_amount_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "world.json"
    bad.write_text(
        json.dumps(
            {"accounts": [{"id": "A", "owner": "o", "tier": "standard",
                           "balance": "-1.00", "frozen": False}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(AppError, match="must not be negative"):
        parse_world(bad)


def test_missing_field_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "scenario.json"
    bad.write_text(
        json.dumps({"steps": [{"action": "decide", "reference": "R-1", "role": "finance"}]}),
        encoding="utf-8",
    )
    with pytest.raises(AppError, match="missing required field 'decision'"):
        parse_scenario(bad)


def test_main_returns_zero_for_a_good_run(tmp_path: Path) -> None:
    code = main(
        [
            "--db",
            str(tmp_path / "x.db"),
            "run",
            str(ROOT / "scenario.json"),
            "--world",
            str(ROOT / "world.json"),
        ]
    )
    assert code == 0

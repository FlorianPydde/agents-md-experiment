from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.cli import main
from app.errors import AppError
from app.wire import load_scenario, load_world

HERE = Path(__file__).resolve().parent.parent

EXPECTED = """\
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
"""


def test_run_matches_the_acceptance_output(tmp_path, capsys) -> None:
    code = main(
        [
            "--db",
            str(tmp_path / "acc.sqlite3"),
            "run",
            str(HERE / "scenario.json"),
            "--world",
            str(HERE / "world.json"),
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == EXPECTED


def test_run_is_repeatable(tmp_path, capsys) -> None:
    argv = [
        "--db",
        str(tmp_path / "twice.sqlite3"),
        "run",
        str(HERE / "scenario.json"),
        "--world",
        str(HERE / "world.json"),
    ]
    main(argv)
    first = capsys.readouterr().out
    main(argv)
    assert capsys.readouterr().out == first


def test_missing_file_is_a_message_not_a_traceback(tmp_path, capsys) -> None:
    code = main(["--db", str(tmp_path / "x.sqlite3"), "run", str(tmp_path / "none.json")])
    assert code == 1
    assert "file not found" in capsys.readouterr().err


def test_malformed_scenario_is_refused(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"steps": [{"action": "decide", "reference": "R", "role": "boss", '
                   '"decision": "approve"}]}')
    with pytest.raises(AppError, match="malformed"):
        load_scenario(bad)


def test_negative_amount_is_refused(tmp_path) -> None:
    bad = tmp_path / "neg.json"
    bad.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "id": "A",
                        "owner": "o",
                        "tier": "standard",
                        "balance": "-1.00",
                        "frozen": False,
                    }
                ]
            }
        )
    )
    with pytest.raises(AppError, match="malformed"):
        load_world(bad)


def test_missing_field_is_refused(tmp_path) -> None:
    bad = tmp_path / "missing.json"
    bad.write_text('{"steps": [{"action": "intake", "request": {"reference": "R"}}]}')
    with pytest.raises(AppError, match="malformed"):
        load_scenario(bad)


def test_invalid_json_is_refused(tmp_path) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{ not json")
    with pytest.raises(AppError, match="not valid JSON"):
        load_scenario(bad)


def test_show_unknown_reference_exits_non_zero(tmp_path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app", "--db", str(tmp_path / "s.sqlite3"),
         "show", "REQ-MISSING"],
        cwd=HERE,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "REQ-MISSING" in result.stderr
    assert "Traceback" not in result.stderr

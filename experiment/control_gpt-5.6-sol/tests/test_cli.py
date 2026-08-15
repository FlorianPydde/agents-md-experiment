from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXPECTED = (
    "REQ-1001  goodwill_credit     completed\n"
    "REQ-1002  goodwill_credit     completed\n"
    "REQ-1003  account_recovery    rejected\n"
    "REQ-1004  collect_debt        failed\n"
    "ACC-100      1500.00  active\n"
    "ACC-200        50.00  frozen\n"
)


def run_cli(*arguments: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "app", *arguments],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_acceptance_run_output_is_exact() -> None:
    result = run_cli("run", "scenario.json", "--world", "world.json")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == EXPECTED


def test_show_export_and_clear_not_found_error(tmp_path: Path) -> None:
    database = tmp_path / "runner.db"
    run_result = run_cli(
        "--database",
        str(database),
        "run",
        "scenario.json",
        "--world",
        "world.json",
    )
    assert run_result.returncode == 0

    show_result = run_cli(
        "--database",
        str(database),
        "show",
        "REQ-1002",
        "REQ-1002",
    )
    shown = json.loads(show_result.stdout)
    assert show_result.returncode == 0
    assert shown[0] == shown[1]
    assert shown[0]["state"] == "completed"

    missing_result = run_cli(
        "--database", str(database), "show", "REQ-DOES-NOT-EXIST"
    )
    assert missing_result.returncode != 0
    assert missing_result.stderr == "error: request not found: REQ-DOES-NOT-EXIST\n"
    assert "Traceback" not in missing_result.stderr

    export_result = run_cli("--database", str(database), "export", "--format", "json")
    assert export_result.returncode == 0
    report = json.loads((ROOT / "report.json").read_text(encoding="utf-8"))
    assert report[0] == {
        "reference": "REQ-1001",
        "kind": "goodwill_credit",
        "state": "completed",
        "account": "ACC-100",
        "amount": "50.00",
    }
    (ROOT / "report.json").unlink()


def test_malformed_input_has_no_traceback(tmp_path: Path) -> None:
    bad_scenario = tmp_path / "bad.json"
    bad_scenario.write_text('{"steps":[{"action":"unknown"}]}', encoding="utf-8")

    result = run_cli("run", str(bad_scenario), "--world", "world.json")

    assert result.returncode != 0
    assert "action must be one of: decide, intake" in result.stderr
    assert "Traceback" not in result.stderr

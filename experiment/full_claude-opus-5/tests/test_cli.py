"""End-to-end test for the `run` command against the supplied scenario."""

import shutil
from pathlib import Path

import pytest

from app.cli import main

FIXTURES_DIR = Path(__file__).resolve().parent.parent
EXPECTED_OUTPUT = """\
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
"""


@pytest.fixture()
def scenario_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copy(FIXTURES_DIR / "world.json", tmp_path / "world.json")
    shutil.copy(FIXTURES_DIR / "scenario.json", tmp_path / "scenario.json")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_run_matches_the_acceptance_output_exactly(scenario_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["run", "scenario.json", "--world", "world.json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == EXPECTED_OUTPUT


def test_export_writes_one_record_per_request(scenario_dir: Path) -> None:
    main(["run", "scenario.json", "--world", "world.json"])
    exit_code = main(["export", "--format", "csv"])

    assert exit_code == 0
    lines = (scenario_dir / "report.csv").read_text().splitlines()
    assert len(lines) == 5  # header + 4 requests
    assert lines[0] == "reference,kind,state,account,amount"


def test_show_exits_non_zero_for_unknown_reference(
    scenario_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["run", "scenario.json", "--world", "world.json"])
    exit_code = main(["show", "REQ-DOES-NOT-EXIST"])

    assert exit_code == 1
    assert "REQ-DOES-NOT-EXIST" in capsys.readouterr().err

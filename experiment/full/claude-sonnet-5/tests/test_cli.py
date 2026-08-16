"""Tests for the CLI, run against the real world.json/scenario.json fixtures."""

import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

EXPECTED_OUTPUT = """\
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen"""


def run_cli(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "app", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_run_command_matches_acceptance_output(tmp_path):
    world = json.loads((BASE_DIR / "world.json").read_text())
    scenario = json.loads((BASE_DIR / "scenario.json").read_text())

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    (work_dir / "world.json").write_text(json.dumps(world))
    (work_dir / "scenario.json").write_text(json.dumps(scenario))
    (work_dir / "app").symlink_to(BASE_DIR / "app")

    result = run_cli("run", "scenario.json", "--world", "world.json", cwd=work_dir)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == EXPECTED_OUTPUT


def test_run_command_reports_missing_file_cleanly(tmp_path):
    world = json.loads((BASE_DIR / "world.json").read_text())

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    (work_dir / "world.json").write_text(json.dumps(world))
    (work_dir / "app").symlink_to(BASE_DIR / "app")

    result = run_cli("run", "missing_scenario.json", "--world", "world.json", cwd=work_dir)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "missing_scenario.json" in result.stderr


def test_show_command_reports_unknown_reference(tmp_path):
    world = json.loads((BASE_DIR / "world.json").read_text())
    scenario = json.loads((BASE_DIR / "scenario.json").read_text())

    work_dir = tmp_path / "workdir"
    work_dir.mkdir()
    (work_dir / "world.json").write_text(json.dumps(world))
    (work_dir / "scenario.json").write_text(json.dumps(scenario))
    (work_dir / "app").symlink_to(BASE_DIR / "app")

    run_cli("run", "scenario.json", "--world", "world.json", cwd=work_dir)
    result = run_cli("show", "NOPE", cwd=work_dir)
    assert result.returncode != 0
    assert "NOPE" in result.stderr

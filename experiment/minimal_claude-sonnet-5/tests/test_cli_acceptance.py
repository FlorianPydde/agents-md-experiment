"""End-to-end test: run the supplied scenario.json/world.json and check the
exact acceptance output from SPEC.md."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_OUTPUT = (
    "REQ-1001  goodwill_credit     completed\n"
    "REQ-1002  goodwill_credit     completed\n"
    "REQ-1003  account_recovery    rejected\n"
    "REQ-1004  collect_debt        failed\n"
    "ACC-100      1500.00  active\n"
    "ACC-200        50.00  frozen\n"
)


def test_run_command_matches_acceptance_output(tmp_path):
    world_src = PROJECT_ROOT / "world.json"
    scenario_src = PROJECT_ROOT / "scenario.json"
    world_dst = tmp_path / "world.json"
    scenario_dst = tmp_path / "scenario.json"
    world_dst.write_text(world_src.read_text(encoding="utf-8"), encoding="utf-8")
    scenario_dst.write_text(scenario_src.read_text(encoding="utf-8"), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "app", "run", "scenario.json", "--world", "world.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(PROJECT_ROOT), **_base_env()},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == EXPECTED_OUTPUT


def _base_env():
    import os

    return dict(os.environ)

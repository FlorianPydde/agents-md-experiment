"""Acceptance test: running the supplied scenario/world through the CLI
must produce the exact output specified in SPEC.md.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_OUTPUT = (
    "REQ-1001  goodwill_credit     completed\n"
    "REQ-1002  goodwill_credit     completed\n"
    "REQ-1003  account_recovery    rejected\n"
    "REQ-1004  collect_debt        failed\n"
    "ACC-100      1500.00  active\n"
    "ACC-200        50.00  frozen\n"
)


def test_run_matches_acceptance_output(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app",
            "run",
            str(REPO_ROOT / "scenario.json"),
            "--world",
            str(REPO_ROOT / "world.json"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == EXPECTED_OUTPUT

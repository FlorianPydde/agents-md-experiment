"""End to end tests for the `run` command against the supplied scenario, and
for `show` and `export`."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from app.cmd_export import export_command
from app.cmd_run import run_command
from app.cmd_show import show_command

REPO_DIR = Path(__file__).resolve().parents[1]

EXPECTED_OUTPUT = """\
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen"""


def test_run_matches_acceptance_output(tmp_path):
    db_path = tmp_path / "log.db"
    output = run_command(
        str(REPO_DIR / "scenario.json"), str(REPO_DIR / "world.json"), db_path=str(db_path)
    )
    assert output == EXPECTED_OUTPUT


def test_show_unknown_reference_raises(tmp_path):
    from app.errors import AppError

    db_path = tmp_path / "log.db"
    run_command(str(REPO_DIR / "scenario.json"), str(REPO_DIR / "world.json"), db_path=str(db_path))
    try:
        show_command(["REQ-NOPE"], db_path=str(db_path))
    except AppError as exc:
        assert "REQ-NOPE" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected AppError")


def test_show_caches_repeated_lookups(tmp_path, monkeypatch):
    db_path = tmp_path / "log.db"
    run_command(str(REPO_DIR / "scenario.json"), str(REPO_DIR / "world.json"), db_path=str(db_path))

    from app import cmd_show

    calls = []
    original = cmd_show._build_report

    def counting_build_report(engine, reference):
        calls.append(reference)
        return original(engine, reference)

    monkeypatch.setattr(cmd_show, "_build_report", counting_build_report)
    cmd_show.show_command(["REQ-1002", "REQ-1002", "REQ-1002"], db_path=str(db_path))
    assert calls == ["REQ-1002"]


def test_export_csv(tmp_path):
    db_path = tmp_path / "log.db"
    run_command(str(REPO_DIR / "scenario.json"), str(REPO_DIR / "world.json"), db_path=str(db_path))
    export_command("csv", db_path=str(db_path), out_dir=str(tmp_path))

    with (tmp_path / "report.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["reference"] for r in rows] == ["REQ-1001", "REQ-1002", "REQ-1003", "REQ-1004"]
    assert rows[0]["state"] == "completed"
    assert rows[3]["state"] == "failed"


def test_export_json(tmp_path):
    db_path = tmp_path / "log.db"
    run_command(str(REPO_DIR / "scenario.json"), str(REPO_DIR / "world.json"), db_path=str(db_path))
    export_command("json", db_path=str(db_path), out_dir=str(tmp_path))

    records = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert len(records) == 4
    assert records[2]["state"] == "rejected"

import json
import subprocess
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent

EXPECTED_OUTPUT = """\
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
"""


def run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "app", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_run_matches_the_acceptance_output(tmp_path):
    result = run_cli(
        ["run", str(FOLDER / "scenario.json"), "--world", str(FOLDER / "world.json"),
         "--db", str(tmp_path / "governed.db")],
        cwd=FOLDER,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == EXPECTED_OUTPUT


def test_show_unknown_reference_exits_non_zero(tmp_path):
    db_path = tmp_path / "governed.db"
    run_cli(
        ["run", str(FOLDER / "scenario.json"), "--world", str(FOLDER / "world.json"), "--db", str(db_path)],
        cwd=FOLDER,
    )
    result = run_cli(["show", "REQ-NOPE", "--db", str(db_path)], cwd=FOLDER)
    assert result.returncode != 0
    assert "REQ-NOPE" in result.stderr


def test_show_repeated_reference_uses_the_cache(tmp_path, monkeypatch):
    from app import cli

    db_path = tmp_path / "governed.db"
    run_cli(
        ["run", str(FOLDER / "scenario.json"), "--world", str(FOLDER / "world.json"), "--db", str(db_path)],
        cwd=FOLDER,
    )

    calls = []
    original = cli._fetch_show_bundle

    def counting_fetch(conn, reference):
        calls.append(reference)
        return original(conn, reference)

    monkeypatch.setattr(cli, "_fetch_show_bundle", counting_fetch)

    args = type("Args", (), {"references": ["REQ-1001", "REQ-1001"], "db": str(db_path)})()
    cli.cmd_show(args)

    assert calls == ["REQ-1001"]


def test_export_writes_expected_records(tmp_path):
    db_path = tmp_path / "governed.db"
    run_cli(
        ["run", str(FOLDER / "scenario.json"), "--world", str(FOLDER / "world.json"), "--db", str(db_path)],
        cwd=tmp_path,
    )
    result = run_cli(["export", "--format", "json", "--db", str(db_path)], cwd=tmp_path)
    assert result.returncode == 0, result.stderr

    report = json.loads((tmp_path / "report.json").read_text())
    assert report == [
        {"reference": "REQ-1001", "kind": "goodwill_credit", "state": "completed", "account": "ACC-100", "amount": "50.00"},
        {"reference": "REQ-1002", "kind": "goodwill_credit", "state": "completed", "account": "ACC-100", "amount": "250.00"},
        {"reference": "REQ-1003", "kind": "account_recovery", "state": "rejected", "account": "ACC-200", "amount": "75.00"},
        {"reference": "REQ-1004", "kind": "collect_debt", "state": "failed", "account": "ACC-200", "amount": "500.00"},
    ]


def test_run_reports_missing_file_clearly(tmp_path):
    result = run_cli(
        ["run", "does-not-exist.json", "--world", str(FOLDER / "world.json"), "--db", str(tmp_path / "governed.db")],
        cwd=FOLDER,
    )
    assert result.returncode != 0
    assert "does-not-exist.json" in result.stderr
    assert "Traceback" not in result.stderr

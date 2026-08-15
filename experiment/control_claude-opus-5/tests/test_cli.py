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


def run_cli(*args: str, db: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app", "--db", str(db), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_run_output_matches_acceptance_exactly(tmp_path: Path) -> None:
    result = run_cli("run", "scenario.json", "--world", "world.json", db=tmp_path / "a.db")
    assert result.returncode == 0, result.stderr
    assert result.stdout.replace("\r\n", "\n") == EXPECTED


def test_run_is_repeatable(tmp_path: Path) -> None:
    db = tmp_path / "b.db"
    first = run_cli("run", "scenario.json", "--world", "world.json", db=db)
    second = run_cli("run", "scenario.json", "--world", "world.json", db=db)
    assert first.stdout == second.stdout


def test_show_missing_reference_fails_without_traceback(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    run_cli("run", "scenario.json", "--world", "world.json", db=db)
    result = run_cli("show", "REQ-9999", db=db)
    assert result.returncode != 0
    assert "REQ-9999" in result.stderr
    assert "Traceback" not in result.stderr


def test_show_reports_a_known_request(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    run_cli("run", "scenario.json", "--world", "world.json", db=db)
    result = run_cli("show", "REQ-1002", "REQ-1002", db=db)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("reference: REQ-1002") == 2
    assert "apply_credit" in result.stdout
    assert "finance" in result.stdout


def test_export_writes_each_format(tmp_path: Path) -> None:
    db = tmp_path / "e.db"
    run_cli("run", "scenario.json", "--world", "world.json", db=db)
    for fmt in ("csv", "json", "tsv"):
        out = tmp_path / f"report.{fmt}"
        result = run_cli("export", "--format", fmt, "--out", str(out), db=db)
        assert result.returncode == 0, result.stderr
        text = out.read_text(encoding="utf-8")
        assert "REQ-1004" in text
        assert "collect_debt" in text


def test_missing_world_file_is_a_clean_error(tmp_path: Path) -> None:
    result = run_cli("run", "scenario.json", "--world", "nope.json", db=tmp_path / "f.db")
    assert result.returncode != 0
    assert "not found" in result.stderr
    assert "Traceback" not in result.stderr

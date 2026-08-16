from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from app.__main__ import main
from app.domain import EventType, RequestState
from app.engine import Engine
from app.exporting import ExportFormat, export
from app.reporting import DetailReader, detail_lines
from app.store import Store

FOLDER = Path(__file__).resolve().parent.parent

ACCEPTANCE = """REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
"""


def test_run_prints_the_acceptance_output(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "--db",
            str(database),
            "run",
            str(FOLDER / "scenario.json"),
            "--world",
            str(FOLDER / "world.json"),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == ACCEPTANCE


def test_run_is_repeatable(database: Path, capsys: pytest.CaptureFixture[str]) -> None:
    arguments = [
        "--db",
        str(database),
        "run",
        str(FOLDER / "scenario.json"),
        "--world",
        str(FOLDER / "world.json"),
    ]
    main(arguments)
    first = capsys.readouterr().out
    main(arguments)

    assert capsys.readouterr().out == first


def test_a_missing_file_exits_non_zero_without_a_traceback(
    database: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        ["--db", str(database), "run", str(tmp_path / "absent.json"), "--world", str(FOLDER / "world.json")]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: file not found")
    assert "Traceback" not in captured.err


def test_show_reports_an_unknown_reference(
    replayed: Engine, database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replayed.store.close()

    exit_code = main(["--db", str(database), "show", "REQ-9999"])

    assert exit_code == 1
    assert "REQ-9999" in capsys.readouterr().err


def test_show_looks_a_repeated_reference_up_once(replayed: Engine) -> None:
    reader = DetailReader(replayed.store)

    for reference in ("REQ-1001", "REQ-1002", "REQ-1001", "REQ-1001"):
        detail_lines(reader.detail(reference))

    assert reader.lookups == 2


def test_the_log_survives_between_runs(replayed: Engine, database: Path) -> None:
    replayed.store.close()

    with Store(database) as reopened:
        events = reopened.events_for("REQ-1003")

    assert events[0].type is EventType.REQUEST_RECEIVED
    assert events[-1].type is EventType.REQUEST_FINISHED
    assert dict(events[-1].details)["state"] == RequestState.REJECTED.value


def test_the_log_cannot_be_edited_or_deleted(replayed: Engine, database: Path) -> None:
    replayed.store.close()
    connection = sqlite3.connect(database)

    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        connection.execute("UPDATE events SET type = 'tampered'")
    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        connection.execute("DELETE FROM events")

    connection.close()


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_export_writes_one_record_per_request(replayed: Engine, fmt: ExportFormat, tmp_path: Path) -> None:
    target = export(replayed.store.records(), fmt, tmp_path)

    assert target.name == f"report.{fmt.value}"
    if fmt is ExportFormat.JSON:
        rows = json.loads(target.read_text())
    else:
        delimiter = "," if fmt is ExportFormat.CSV else "\t"
        rows = list(csv.DictReader(target.read_text().splitlines(), delimiter=delimiter))
    assert [row["reference"] for row in rows] == ["REQ-1001", "REQ-1002", "REQ-1003", "REQ-1004"]
    assert rows[3]["state"] == "failed"
    assert rows[1]["amount"] == "250.00"


def test_export_command_writes_into_a_folder(replayed: Engine, database: Path, tmp_path: Path) -> None:
    replayed.store.close()

    exit_code = main(["--db", str(database), "export", "--format", "tsv", "--into", str(tmp_path)])

    assert exit_code == 0
    assert (tmp_path / "report.tsv").exists()

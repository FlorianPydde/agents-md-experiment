from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from conftest import make_request

from app.engine import Runner
from app.enums import ExportFormat, RequestKind
from app.export import EXPORTERS, write_report


@pytest.fixture
def records(runner: Runner, store):
    runner.intake(make_request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00"))
    runner.intake(make_request("REQ-2", RequestKind.COLLECT_DEBT, "ACC-200", "10.00"))
    return store.all_requests()


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_every_format_writes_a_file(records, fmt: ExportFormat, tmp_path: Path) -> None:
    target = write_report(records, fmt, tmp_path)
    assert target.name == f"report.{EXPORTERS[fmt].suffix}"
    assert target.read_text(encoding="utf-8")


def test_csv_holds_one_record_per_request(records, tmp_path: Path) -> None:
    target = write_report(records, ExportFormat.CSV, tmp_path)
    rows = list(csv.DictReader(io.StringIO(target.read_text(encoding="utf-8"))))
    assert [r["reference"] for r in rows] == ["REQ-1", "REQ-2"]
    assert rows[0]["kind"] == "goodwill_credit"
    assert rows[0]["state"] == "completed"
    assert rows[0]["account"] == "ACC-100"
    assert rows[0]["amount"] == "50.00"


def test_json_holds_the_same_fields(records, tmp_path: Path) -> None:
    target = write_report(records, ExportFormat.JSON, tmp_path)
    rows = json.loads(target.read_text(encoding="utf-8"))
    assert set(rows[0]) == {"reference", "kind", "state", "account", "amount"}


def test_tsv_is_tab_separated(records, tmp_path: Path) -> None:
    target = write_report(records, ExportFormat.TSV, tmp_path)
    header = target.read_text(encoding="utf-8").splitlines()[0]
    assert header.split("\t")[0] == "reference"

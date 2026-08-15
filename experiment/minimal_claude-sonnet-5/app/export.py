"""Export formats: CSV, JSON, TSV.

New formats are added by registering an ExportFn in EXPORTERS.
"""

from __future__ import annotations

import csv
import json
from enum import StrEnum
from io import StringIO
from typing import Callable

from app.reports import ExportRecord


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    TSV = "tsv"


ExportFn = Callable[[list[ExportRecord]], str]

FIELDNAMES = ["reference", "kind", "state", "account", "amount"]


def _record_row(record: ExportRecord) -> dict[str, str]:
    return {
        "reference": record.reference,
        "kind": record.kind.value,
        "state": record.state.value,
        "account": record.account,
        "amount": f"{record.amount:.2f}",
    }


def export_csv(records: list[ExportRecord]) -> str:
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(_record_row(record))
    return buf.getvalue()


def export_tsv(records: list[ExportRecord]) -> str:
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(_record_row(record))
    return buf.getvalue()


def export_json(records: list[ExportRecord]) -> str:
    return json.dumps([_record_row(r) for r in records], indent=2) + "\n"


EXPORTERS: dict[ExportFormat, ExportFn] = {
    ExportFormat.CSV: export_csv,
    ExportFormat.JSON: export_json,
    ExportFormat.TSV: export_tsv,
}

FILE_NAMES: dict[ExportFormat, str] = {
    ExportFormat.CSV: "report.csv",
    ExportFormat.JSON: "report.json",
    ExportFormat.TSV: "report.tsv",
}

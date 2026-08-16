"""Export: one record per request, in one of the supported formats."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from .domain import RequestRecord
from .money import format_amount

FIELDS = ("reference", "kind", "state", "account", "amount")


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    TSV = "tsv"


@dataclass(frozen=True, slots=True)
class ReportRow:
    reference: str
    kind: str
    state: str
    account: str
    amount: str


def rows_for(records: Sequence[RequestRecord]) -> list[ReportRow]:
    return [
        ReportRow(
            reference=record.request.reference,
            kind=record.request.kind.value,
            state=record.state.value,
            account=record.request.account,
            amount=format_amount(record.request.amount),
        )
        for record in records
    ]


def _write_delimited(rows: Sequence[ReportRow], target: Path, delimiter: str) -> None:
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FIELDS), delimiter=delimiter)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_csv(rows: Sequence[ReportRow], target: Path) -> None:
    _write_delimited(rows, target, ",")


def _write_tsv(rows: Sequence[ReportRow], target: Path) -> None:
    _write_delimited(rows, target, "\t")


def _write_json(rows: Sequence[ReportRow], target: Path) -> None:
    target.write_text(
        json.dumps([asdict(row) for row in rows], indent=2) + "\n", encoding="utf-8"
    )


Writer = Callable[[Sequence[ReportRow], Path], None]

WRITERS: dict[ExportFormat, Writer] = {
    ExportFormat.CSV: _write_csv,
    ExportFormat.JSON: _write_json,
    ExportFormat.TSV: _write_tsv,
}


def export(records: Sequence[RequestRecord], fmt: ExportFormat, directory: Path) -> Path:
    """Write the report and return the file it was written to."""
    target = directory / f"report.{fmt.value}"
    WRITERS[fmt](rows_for(records), target)
    return target

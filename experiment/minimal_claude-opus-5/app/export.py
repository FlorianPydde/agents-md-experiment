"""Export the request records. Formats live in a registry keyed by an enum."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .store import RequestRecord


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    TSV = "tsv"


COLUMNS = ("reference", "kind", "state", "account", "amount")


@dataclass(frozen=True)
class ExportRow:
    reference: str
    kind: str
    state: str
    account: str
    amount: str

    def values(self) -> tuple[str, ...]:
        return (self.reference, self.kind, self.state, self.account, self.amount)


def to_rows(records: tuple[RequestRecord, ...]) -> tuple[ExportRow, ...]:
    return tuple(
        ExportRow(
            reference=record.reference,
            kind=record.kind.value,
            state=record.state.value,
            account=record.account,
            amount=f"{record.amount:.2f}",
        )
        for record in records
    )


def _write_delimited(path: Path, rows: tuple[ExportRow, ...], delimiter: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow(row.values())


def _export_csv(path: Path, rows: tuple[ExportRow, ...]) -> None:
    _write_delimited(path, rows, ",")


def _export_tsv(path: Path, rows: tuple[ExportRow, ...]) -> None:
    _write_delimited(path, rows, "\t")


def _export_json(path: Path, rows: tuple[ExportRow, ...]) -> None:
    payload = [dict(zip(COLUMNS, row.values(), strict=True)) for row in rows]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


ExportFn = Callable[[Path, tuple[ExportRow, ...]], None]

EXPORTERS: dict[ExportFormat, ExportFn] = {
    ExportFormat.CSV: _export_csv,
    ExportFormat.JSON: _export_json,
    ExportFormat.TSV: _export_tsv,
}


def export(fmt: ExportFormat, directory: Path, records: tuple[RequestRecord, ...]) -> Path:
    path = directory / f"report.{fmt.value}"
    EXPORTERS[fmt](path, to_rows(records))
    return path

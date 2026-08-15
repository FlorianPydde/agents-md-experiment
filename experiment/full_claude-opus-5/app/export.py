"""Report export. One registry entry per format; adding one is one entry."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .domain import RequestRecord
from .enums import ExportFormat


@dataclass(frozen=True)
class ReportRow:
    reference: str
    kind: str
    state: str
    account: str
    amount: str

    @classmethod
    def of(cls, record: RequestRecord) -> ReportRow:
        request = record.request
        return cls(
            reference=request.reference,
            kind=str(request.kind),
            state=str(record.state),
            account=request.account_id,
            amount=request.amount.formatted(),
        )

    def as_mapping(self) -> dict[str, str]:
        return {
            "reference": self.reference,
            "kind": self.kind,
            "state": self.state,
            "account": self.account,
            "amount": self.amount,
        }


RenderFn = Callable[[tuple[ReportRow, ...]], str]

FIELDS = ("reference", "kind", "state", "account", "amount")


def _delimited(rows: tuple[ReportRow, ...], delimiter: str) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=FIELDS, delimiter=delimiter, lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row.as_mapping())
    return buffer.getvalue()


def render_csv(rows: tuple[ReportRow, ...]) -> str:
    return _delimited(rows, ",")


def render_tsv(rows: tuple[ReportRow, ...]) -> str:
    return _delimited(rows, "\t")


def render_json(rows: tuple[ReportRow, ...]) -> str:
    return json.dumps([r.as_mapping() for r in rows], indent=2) + "\n"


@dataclass(frozen=True)
class Exporter:
    suffix: str
    render: RenderFn


EXPORTERS: dict[ExportFormat, Exporter] = {
    ExportFormat.CSV: Exporter("csv", render_csv),
    ExportFormat.JSON: Exporter("json", render_json),
    ExportFormat.TSV: Exporter("tsv", render_tsv),
}

if _missing := set(ExportFormat) - EXPORTERS.keys():
    raise RuntimeError(f"No exporter registered for: {_missing}")


def write_report(
    records: tuple[RequestRecord, ...], fmt: ExportFormat, directory: Path
) -> Path:
    exporter = EXPORTERS[fmt]
    target = directory / f"report.{exporter.suffix}"
    target.write_text(
        exporter.render(tuple(ReportRow.of(r) for r in records)), encoding="utf-8"
    )
    return target

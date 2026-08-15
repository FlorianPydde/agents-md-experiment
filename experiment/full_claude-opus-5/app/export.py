"""Export registry: write a report of requests in a chosen format.

Adding a format means adding one registry entry; rule 18's coverage check
fails fast if a format is left unregistered.
"""

from __future__ import annotations

import csv
import json
from enum import StrEnum
from pathlib import Path
from typing import Callable

from app.domain import RequestRecord


class Format(StrEnum):
    CSV = "csv"
    JSON = "json"
    TSV = "tsv"


def _fields(records: list[RequestRecord]) -> list[dict[str, str]]:
    return [
        {
            "reference": record.request.reference,
            "kind": record.request.kind.value,
            "state": record.state.value,
            "account": record.request.account_id,
            "amount": record.request.amount.formatted(),
        }
        for record in records
    ]


_COLUMNS = ["reference", "kind", "state", "account", "amount"]


def _export_delimited(records: list[RequestRecord], path: Path, delimiter: str) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(_fields(records))


def _export_csv(records: list[RequestRecord], path: Path) -> None:
    _export_delimited(records, path, delimiter=",")


def _export_tsv(records: list[RequestRecord], path: Path) -> None:
    _export_delimited(records, path, delimiter="\t")


def _export_json(records: list[RequestRecord], path: Path) -> None:
    with path.open("w") as handle:
        json.dump(_fields(records), handle, indent=2)
        handle.write("\n")


EXPORTERS: dict[Format, Callable[[list[RequestRecord], Path], None]] = {
    Format.CSV: _export_csv,
    Format.JSON: _export_json,
    Format.TSV: _export_tsv,
}

if missing := set(Format) - EXPORTERS.keys():
    raise RuntimeError(f"No exporter registered for: {missing}")


def export(fmt: Format, records: list[RequestRecord], directory: Path) -> Path:
    path = directory / f"report.{fmt.value}"
    EXPORTERS[fmt](records, path)
    return path

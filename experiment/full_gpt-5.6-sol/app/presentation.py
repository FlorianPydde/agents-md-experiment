from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .domain import ExportFormat, NotFoundError
from .storage import RequestRecord, Store


def request_summary(record: RequestRecord) -> dict[str, str]:
    request = record.request
    return {
        "reference": request.reference,
        "kind": request.kind.value,
        "state": record.state.value,
        "account": request.account_id,
        "amount": request.amount.display(),
    }


def request_details(store: Store, reference: str) -> dict[str, Any]:
    record = store.get_request(reference)
    if record is None:
        raise NotFoundError(f"request {reference} does not exist")
    return {
        **request_summary(record),
        "requester": {
            "name": record.request.requester.name,
            "role": record.request.requester.role,
            "origin": record.request.requester.origin.value,
        },
        "steps": [
            {
                "position": step.position,
                "operation": step.operation.value,
                "state": step.state.value,
            }
            for step in store.get_steps(reference)
        ],
        "approvals": [
            {
                "operation": approval.operation.value,
                "required_role": approval.required_role.value,
                "state": approval.state.value,
                "decided_by": (
                    approval.decided_by.value if approval.decided_by else None
                ),
            }
            for approval in store.list_approvals(reference)
        ],
        "log": store.get_events(reference),
    }


def _write_delimited(
    records: list[dict[str, str]], path: Path, delimiter: str
) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=["reference", "kind", "state", "account", "amount"],
            delimiter=delimiter,
        )
        writer.writeheader()
        writer.writerows(records)


def _export_csv(records: list[dict[str, str]], path: Path) -> None:
    _write_delimited(records, path, ",")


def _export_tsv(records: list[dict[str, str]], path: Path) -> None:
    _write_delimited(records, path, "\t")


def _export_json(records: list[dict[str, str]], path: Path) -> None:
    path.write_text(
        json.dumps(records, indent=2) + "\n",
        encoding="utf-8",
    )


ExportWriter = Callable[[list[dict[str, str]], Path], None]

EXPORTERS: dict[ExportFormat, ExportWriter] = {
    ExportFormat.CSV: _export_csv,
    ExportFormat.JSON: _export_json,
    ExportFormat.TSV: _export_tsv,
}

if missing_exporters := set(ExportFormat) - EXPORTERS.keys():
    raise RuntimeError(f"No exporter registered for: {missing_exporters}")


def export_report(store: Store, export_format: ExportFormat) -> Path:
    records = [request_summary(record) for record in store.list_requests()]
    path = Path(f"report.{export_format.value}")
    EXPORTERS[export_format](records, path)
    return path

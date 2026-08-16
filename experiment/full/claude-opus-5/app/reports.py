"""Export. One record per request, in one of the registered formats."""

import csv
import io
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from app.domain import TrackedRequest
from app.enums import ExportFormat

FIELDS = ("reference", "kind", "state", "account", "amount")


@dataclass(frozen=True)
class ExportRecord:
    """One exported request."""

    reference: str
    kind: str
    state: str
    account: str
    amount: str


def record_of(tracked: TrackedRequest) -> ExportRecord:
    request = tracked.request
    return ExportRecord(
        reference=request.reference,
        kind=request.kind.value,
        state=tracked.state.value,
        account=request.account,
        amount=str(request.amount),
    )


def render_csv(records: tuple[ExportRecord, ...]) -> str:
    return _delimited(records, delimiter=",")


def render_tsv(records: tuple[ExportRecord, ...]) -> str:
    return _delimited(records, delimiter="\t")


def render_json(records: tuple[ExportRecord, ...]) -> str:
    return json.dumps([asdict(record) for record in records], indent=2) + "\n"


def _delimited(records: tuple[ExportRecord, ...], delimiter: str) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=FIELDS, delimiter=delimiter, lineterminator="\n"
    )
    writer.writeheader()
    for record in records:
        writer.writerow(asdict(record))
    return buffer.getvalue()


type Renderer = Callable[[tuple[ExportRecord, ...]], str]

RENDERERS: dict[ExportFormat, Renderer] = {
    ExportFormat.CSV: render_csv,
    ExportFormat.JSON: render_json,
    ExportFormat.TSV: render_tsv,
}

if missing := set(ExportFormat) - RENDERERS.keys():
    raise RuntimeError(f"No renderer registered for: {missing}")


def write_report(
    tracked_requests: tuple[TrackedRequest, ...],
    export_format: ExportFormat,
    folder: Path,
) -> Path:
    records = tuple(record_of(tracked) for tracked in tracked_requests)
    destination = folder / f"report.{export_format.value}"
    destination.write_text(RENDERERS[export_format](records))
    return destination

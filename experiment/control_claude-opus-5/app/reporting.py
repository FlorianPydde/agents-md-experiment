"""Rendering: the run summary, the show view, and export formats."""

from __future__ import annotations

import csv
import io
import json
from typing import Callable, Mapping, Sequence

from .errors import InputError
from .models import Account, Request
from .store import LogEntry


def summary_lines(requests: Sequence[Request], accounts: Sequence[Account]) -> list[str]:
    """One line per request in arrival order, then one per account by id."""
    lines = [
        f"{request.reference:<10}{request.kind.value:<20}{request.state.value}"
        for request in requests
    ]
    lines.extend(
        f"{account.id:<10}{format(account.balance, '.2f'):>10}  "
        f"{'frozen' if account.frozen else 'active'}"
        for account in sorted(accounts, key=lambda item: item.id)
    )
    return lines


def show_lines(request: Request, entries: Sequence[LogEntry]) -> list[str]:
    lines = [
        f"reference: {request.reference}",
        f"kind:      {request.kind.value}",
        f"account:   {request.account}",
        f"amount:    {format(request.amount, '.2f')}",
        f"requester: {request.requester.name} ({request.requester.role}, {request.requester.origin.value})",
        f"state:     {request.state.value}",
        "steps:",
    ]
    for step in request.steps:
        line = f"  {step.index}. {step.operation:<18} {step.state.value}"
        if step.detail:
            line += f" -- {step.detail}"
        lines.append(line)

    lines.append("approvals:")
    if not request.approvals:
        lines.append("  (none)")
    for approval in request.approvals:
        decided = f" by {approval.decided_by.value}" if approval.decided_by else ""
        lines.append(
            f"  step {approval.step_index} {approval.operation:<18} "
            f"requires {approval.required_role.value:<10} {approval.state.value}{decided}"
        )

    lines.append("log:")
    for entry in entries:
        lines.append(f"  {entry.seq:>4} {entry.event:<20} {json.dumps(entry.data, sort_keys=False)}")
    return lines


def _records(requests: Sequence[Request]) -> list[dict[str, str]]:
    return [
        {
            "reference": request.reference,
            "kind": request.kind.value,
            "state": request.state.value,
            "account": request.account,
            "amount": format(request.amount, ".2f"),
        }
        for request in requests
    ]


FIELDS = ("reference", "kind", "state", "account", "amount")


def _delimited(records: Sequence[Mapping[str, str]], delimiter: str) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, delimiter=delimiter, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue()


# New formats are added by registering another renderer here.
FORMATS: dict[str, Callable[[Sequence[Mapping[str, str]]], str]] = {
    "csv": lambda records: _delimited(records, ","),
    "tsv": lambda records: _delimited(records, "\t"),
    "json": lambda records: json.dumps(list(records), indent=2) + "\n",
}


def render_export(requests: Sequence[Request], fmt: str) -> str:
    try:
        renderer = FORMATS[fmt]
    except KeyError:
        allowed = ", ".join(sorted(FORMATS))
        raise InputError(f"unknown export format {fmt!r}; supported formats: {allowed}") from None
    return renderer(_records(requests))

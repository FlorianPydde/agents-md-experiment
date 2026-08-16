"""Exporting one record per request.

Writers live in a registry keyed by format name, so a further format is one
function and one decorator.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Callable

from .errors import AppError
from .store import Store

FIELDS = ("reference", "kind", "state", "account", "amount")

WRITERS: dict[str, Callable[[list[dict[str, str]]], str]] = {}


def writer(name: str) -> Callable[[Callable[[list[dict[str, str]]], str]], Callable]:
    def register(function: Callable[[list[dict[str, str]]], str]) -> Callable:
        WRITERS[name] = function
        return function

    return register


def formats() -> list[str]:
    return sorted(WRITERS)


def records(store: Store) -> list[dict[str, str]]:
    """One record per request, in arrival order."""
    return [
        {
            "reference": request.reference,
            "kind": str(request.kind),
            "state": str(state),
            "account": request.account,
            "amount": f"{request.amount:.2f}",
        }
        for request, state in store.requests()
    ]


def _delimited(rows: list[dict[str, str]], delimiter: str) -> str:
    buffer = io.StringIO()
    output = csv.DictWriter(buffer, fieldnames=list(FIELDS), delimiter=delimiter, lineterminator="\n")
    output.writeheader()
    output.writerows(rows)
    return buffer.getvalue()


@writer("csv")
def _csv(rows: list[dict[str, str]]) -> str:
    return _delimited(rows, ",")


@writer("tsv")
def _tsv(rows: list[dict[str, str]]) -> str:
    return _delimited(rows, "\t")


@writer("json")
def _json(rows: list[dict[str, str]]) -> str:
    return json.dumps(rows, indent=2) + "\n"


def export(store: Store, fmt: str, folder: Path) -> Path:
    """Write `report.<fmt>` into `folder` and report where it went."""
    if fmt not in WRITERS:
        raise AppError(f"unknown format '{fmt}', expected one of: {', '.join(formats())}")
    destination = folder / f"report.{fmt}"
    destination.write_text(WRITERS[fmt](records(store)), encoding="utf-8")
    return destination

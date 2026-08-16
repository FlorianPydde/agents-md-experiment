"""``export``: writes a report of every request to csv, json, or tsv."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

FORMATS = {
    "csv": ("report.csv", ","),
    "tsv": ("report.tsv", "\t"),
}

FIELDS = ("reference", "kind", "state", "account", "amount")


def _fetch_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT reference, kind, state, account, amount FROM requests ORDER BY seq"
    ).fetchall()
    return [dict(row) for row in rows]


def export_report(conn: sqlite3.Connection, fmt: str, output_dir: str | Path = ".") -> str:
    """Write the report file for ``fmt`` under ``output_dir`` and return its path."""

    rows = _fetch_rows(conn)
    directory = Path(output_dir)

    if fmt == "json":
        path = directory / "report.json"
        with open(path, "w", newline="") as fh:
            json.dump(rows, fh, indent=2)
            fh.write("\n")
        return str(path)

    if fmt in FORMATS:
        filename, delimiter = FORMATS[fmt]
        path = directory / filename
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(rows)
        return str(path)

    raise ValueError(f"unknown export format: {fmt!r}")

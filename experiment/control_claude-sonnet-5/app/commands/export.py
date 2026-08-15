"""`export` command: write report.csv / report.json / report.tsv."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from app.store import Store

SUPPORTED_FORMATS = ("csv", "json", "tsv")

FIELDS = ("reference", "kind", "state", "account", "amount")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app export")
    parser.add_argument(
        "--format", required=True, choices=SUPPORTED_FORMATS, help="output format"
    )
    return parser


def run(argv: list[str], *, db_path: Path) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not db_path.exists():
        print(f"error: no database found at {db_path}; run 'run' first", file=sys.stderr)
        sys.exit(1)

    store = Store.open_existing(db_path)
    try:
        records = [
            {
                "reference": row["reference"],
                "kind": row["kind"],
                "state": row["state"],
                "account": row["account"],
                "amount": row["amount"],
            }
            for row in store.list_requests()
        ]
    finally:
        store.close()

    out_path = Path(f"report.{args.format}")
    if args.format == "json":
        out_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    else:
        delimiter = "," if args.format == "csv" else "\t"
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(records)

    print(f"wrote {out_path}")

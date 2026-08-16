"""`python -m app export --format csv|json|tsv`

Writes report.csv, report.json or report.tsv holding one record per request:
its reference, kind, final state, account, and amount.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from app.engine import Engine
from app.errors import AppError

DEFAULT_DB_PATH = "log.db"
FIELDS = ("reference", "kind", "state", "account", "amount")

DELIMITERS = {"csv": ",", "tsv": "\t"}


def _records(engine: Engine) -> list[dict]:
    return [
        {
            "reference": row["reference"],
            "kind": row["kind"],
            "state": row["state"],
            "account": row["account"],
            "amount": row["amount"],
        }
        for row in engine.list_requests()
    ]


def export_command(fmt: str, db_path: str = DEFAULT_DB_PATH, out_dir: str = ".") -> str:
    if fmt not in ("csv", "json", "tsv"):
        raise AppError(f"unsupported export format '{fmt}'")

    engine = Engine.open_existing(db_path)
    records = _records(engine)
    out_path = Path(out_dir) / f"report.{fmt}"

    if fmt == "json":
        out_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    else:
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, delimiter=DELIMITERS[fmt])
            writer.writeheader()
            writer.writerows(records)

    return str(out_path)

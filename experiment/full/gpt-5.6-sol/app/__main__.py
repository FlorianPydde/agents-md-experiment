from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from .api import serve
from .core import DEFAULT_DB, AppError, Store, run_scenario


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("scenario", type=Path)
    run.add_argument("--world", required=True, type=Path)
    show = commands.add_parser("show")
    show.add_argument("references", nargs="+")
    export = commands.add_parser("export")
    export.add_argument("--format", choices=("csv", "json", "tsv"), required=True)
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", default=8000, type=int)
    return parser


def command_run(scenario: Path, world: Path) -> None:
    store = run_scenario(world, scenario)
    try:
        for row in store.connection.execute(
            "SELECT reference, kind, state FROM requests ORDER BY arrival_order"
        ):
            print(f"{row['reference']:<10}{row['kind']:<20}{row['state']}")
        for row in store.connection.execute(
            "SELECT id, balance, frozen FROM accounts ORDER BY id"
        ):
            status = "frozen" if row["frozen"] else "active"
            print(f"{row['id']:<10}{row['balance']:>10}  {status}")
    finally:
        store.close()


def command_show(references: list[str]) -> None:
    store = Store(DEFAULT_DB)
    try:
        store.initialize()

        def lookup(reference: str) -> str:
            return json.dumps(
                store.request_details(reference), indent=2, sort_keys=True
            )

        seen: set[str] = set()
        for reference in references:
            if reference not in seen:
                print(lookup(reference))
                seen.add(reference)
    finally:
        store.close()


def command_export(format_name: str) -> None:
    store = Store(DEFAULT_DB)
    try:
        store.initialize()
        records = store.requests()
    finally:
        store.close()
    output = Path(f"report.{format_name}")
    fields = ["reference", "kind", "state", "account", "amount"]
    if format_name == "json":
        output.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    else:
        delimiter = "," if format_name == "csv" else "\t"
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(records)


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "run":
            command_run(args.scenario, args.world)
        elif args.command == "show":
            command_show(args.references)
        elif args.command == "export":
            command_export(args.format)
        elif args.command == "serve":
            serve(args.host, args.port)
    except (AppError, sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

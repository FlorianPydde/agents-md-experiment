from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .api import serve
from .db import Database
from .errors import AppError
from .service import Service, load_json


ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = ROOT / "service.db"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="python -m app")
    subcommands = result.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="replay a scenario from a clean database")
    run.add_argument("scenario", type=Path)
    run.add_argument("--world", type=Path, required=True)

    show = subcommands.add_parser("show", help="show persisted request details")
    show.add_argument("references", nargs="+")

    export = subcommands.add_parser("export", help="export the request report")
    export.add_argument("--format", choices=("csv", "json", "tsv"), required=True)

    server = subcommands.add_parser("serve", help="serve the HTTP API")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", default=8000, type=int)
    return result


def run_command(service: Service, args: argparse.Namespace) -> None:
    world = load_json(args.world)
    scenario = load_json(args.scenario)
    service.db.reset()
    service.load_world(world)
    service.replay(scenario)
    for request in service.db.connection.execute(
        "SELECT reference, kind, state FROM requests ORDER BY arrival_order"
    ):
        print(f"{request['reference']:<10}{request['kind']:<20}{request['state']}")
    for account in service.db.connection.execute(
        "SELECT id, balance, frozen FROM accounts ORDER BY id"
    ):
        status = "frozen" if account["frozen"] else "active"
        print(f"{account['id']:<10}{float(account['balance']):>10.2f}  {status}")


def show_command(service: Service, args: argparse.Namespace) -> None:
    cache: dict[str, dict[str, Any] | None] = {}
    output = []
    for reference in args.references:
        if reference not in cache:
            detail = service.db.request_detail(reference)
            if detail is not None:
                detail["log"] = service.db.log_entries(reference)
            cache[reference] = detail
        if cache[reference] is None:
            raise AppError(f"request not found: {reference}")
        output.append(cache[reference])
    print(json.dumps(output[0] if len(output) == 1 else output, indent=2))


def _write_delimited(path: Path, rows: list[dict[str, Any]], delimiter: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("reference", "kind", "state", "account", "amount"),
            delimiter=delimiter,
        )
        writer.writeheader()
        writer.writerows(rows)


def export_command(service: Service, args: argparse.Namespace) -> None:
    rows = service.requests()
    path = ROOT / f"report.{args.format}"
    writers: dict[str, Callable[[Path, list[dict[str, Any]]], None]] = {
        "csv": lambda target, records: _write_delimited(target, records, ","),
        "tsv": lambda target, records: _write_delimited(target, records, "\t"),
        "json": lambda target, records: target.write_text(
            json.dumps(records, indent=2) + "\n", encoding="utf-8"
        ),
    }
    writers[args.format](path, rows)
    print(path.name)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    database = Database(DATABASE_PATH)
    service = Service(database)
    commands = {
        "run": run_command,
        "show": show_command,
        "export": export_command,
    }
    try:
        if args.command == "serve":
            serve(service, args.host, args.port)
        else:
            commands[args.command](service, args)
        return 0
    except AppError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        database.close()


from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from .api import ApiServer
from .errors import AppError
from .service import Runner, load_json, validate_scenario, validate_world
from .storage import Store


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = ROOT / "runner.db"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="python -m app")
    result.add_argument(
        "--database", type=Path, default=DEFAULT_DATABASE, help=argparse.SUPPRESS
    )
    commands = result.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Replay a scenario from a clean database")
    run.add_argument("scenario", type=Path)
    run.add_argument("--world", type=Path, required=True)

    show = commands.add_parser("show", help="Show one or more requests")
    show.add_argument("references", nargs="+")

    export = commands.add_parser("export", help="Export the request report")
    export.add_argument("--format", choices=("csv", "json", "tsv"), required=True)

    serve = commands.add_parser("serve", help="Serve the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return result


def command_run(args: argparse.Namespace) -> None:
    accounts = validate_world(load_json(args.world, "world"))
    scenario = validate_scenario(load_json(args.scenario, "scenario"))
    if args.database.exists():
        args.database.unlink()
    store = Store(args.database)
    try:
        store.initialize()
        runner = Runner(store)
        runner.seed_accounts(accounts)
        runner.replay(scenario)
        for request in store.list_requests():
            print(
                f"{request['reference']:<10}{request['kind']:<20}"
                f"{request['state']}"
            )
        rows = store.connection.execute(
            "SELECT id, balance, frozen FROM accounts ORDER BY id"
        ).fetchall()
        for account in rows:
            status = "frozen" if account["frozen"] else "active"
            print(
                f"{account['id']:<10}{Decimal(account['balance']):>10.2f}"
                f"  {status}"
            )
    finally:
        store.close()


def command_show(args: argparse.Namespace) -> None:
    store = open_existing_store(args.database)
    try:
        @lru_cache(maxsize=None)
        def lookup(reference: str) -> dict[str, Any]:
            detail = store.request_detail(reference)
            if detail is None:
                raise AppError(f"request not found: {reference}")
            return detail

        output = [lookup(reference) for reference in args.references]
        print(json.dumps(output[0] if len(output) == 1 else output, indent=2))
    finally:
        store.close()


def command_export(args: argparse.Namespace) -> None:
    store = open_existing_store(args.database)
    try:
        records = store.list_requests()
    finally:
        store.close()
    destination = ROOT / f"report.{args.format}"
    fields = ["reference", "kind", "state", "account", "amount"]
    if args.format == "json":
        destination.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        return
    delimiter = "," if args.format == "csv" else "\t"
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(records)


def command_serve(args: argparse.Namespace) -> None:
    store = open_existing_store(args.database)
    store.close()
    server = ApiServer((args.host, args.port), str(args.database))
    print(f"Serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def open_existing_store(path: Path) -> Store:
    if not path.exists():
        raise AppError(f"database not found: {path}; run a scenario first")
    store = Store(path)
    store.initialize()
    return store


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        {
            "run": command_run,
            "show": command_show,
            "export": command_export,
            "serve": command_serve,
        }[args.command](args)
    except AppError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except sqlite3.Error as error:
        print(f"error: database error: {error}", file=sys.stderr)
        return 1
    return 0

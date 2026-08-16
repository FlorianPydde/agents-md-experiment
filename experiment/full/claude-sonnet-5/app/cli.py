"""Command line interface: run, show, export, serve."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.engine import Engine
from app.errors import AppError
from app.models import load_scenario, load_world
from app.storage import Storage, default_db_path

def _base_dir() -> Path:
    """The folder the database and reports live in: the current working
    directory, since commands are run from within this folder."""

    return Path.cwd()


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise AppError(f"file not found: {path}")
    try:
        text = path.read_text()
    except OSError as exc:
        raise AppError(f"could not read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppError(f"{path} is not valid JSON: {exc}") from exc


def cmd_run(args: argparse.Namespace) -> int:
    scenario_path = Path(args.scenario)
    world_path = Path(args.world)

    world_raw = _read_json(world_path)
    scenario_raw = _read_json(scenario_path)

    accounts = load_world(world_raw)
    entries = load_scenario(scenario_raw)

    db_path = default_db_path(_base_dir())
    storage = Storage.create_fresh(db_path)
    try:
        engine = Engine(storage)
        engine.load_world(accounts)

        for action, entry in entries:
            if action == "intake":
                engine.intake(entry)
            else:
                engine.decide(entry)

        requests = storage.all_requests_in_arrival_order()
        lines = []
        for row in requests:
            lines.append(f"{row['reference']:<10}{row['kind']:<20}{row['state']}")

        accounts_rows = storage.all_accounts()
        for row in accounts_rows:
            balance = Decimal(row["balance"]).quantize(Decimal("0.01"))
            state = "frozen" if row["frozen"] else "active"
            lines.append(f"{row['id']:<10}{balance:>10}  {state}")

        print("\n".join(lines))
    finally:
        storage.close()

    return 0


def _print_request_details(storage: Storage, reference: str) -> None:
    row = storage.get_request(reference)
    if row is None:
        raise AppError(f"no such request: {reference}")

    print(f"Request {row['reference']}")
    print(f"  kind: {row['kind']}")
    print(f"  account: {row['account']}")
    print(f"  amount: {Decimal(row['amount']).quantize(Decimal('0.01'))}")
    print(
        f"  requester: {row['requester_name']} "
        f"({row['requester_role']}, {row['requester_origin']})"
    )
    print(f"  state: {row['state']}")

    print("  steps:")
    for step in storage.steps_for_request(reference):
        print(f"    {step['step_index']}: {step['operation']} -> {step['state']}")

    approvals = storage.approvals_for_request(reference)
    print("  approvals:")
    for approval in approvals:
        print(
            f"    step {approval['step_index']}: role={approval['role_required']} "
            f"status={approval['status']} decided_by={approval['decided_by_role']}"
        )

    print("  log:")
    for entry in storage.log_for_request(reference):
        data = json.loads(entry["data"])
        print(f"    {entry['seq']}: {entry['type']} {json.dumps(data, sort_keys=True)}")


def cmd_show(args: argparse.Namespace) -> int:
    db_path = default_db_path(_base_dir())
    storage = Storage.open_existing(db_path)
    try:
        seen: set[str] = set()
        first = True
        for reference in args.references:
            if reference in seen:
                # Already looked up in this run; do not repeat the work.
                continue
            seen.add(reference)
            if not first:
                print()
            first = False
            _print_request_details(storage, reference)
    finally:
        storage.close()
    return 0


EXPORT_FORMATS = {"csv", "json", "tsv"}


def cmd_export(args: argparse.Namespace) -> int:
    fmt = args.format
    if fmt not in EXPORT_FORMATS:
        raise AppError(f"unsupported export format: {fmt!r}")

    db_path = default_db_path(_base_dir())
    storage = Storage.open_existing(db_path)
    try:
        rows = storage.all_requests_in_arrival_order()
        records = [
            {
                "reference": row["reference"],
                "kind": row["kind"],
                "state": row["state"],
                "account": row["account"],
                "amount": str(Decimal(row["amount"]).quantize(Decimal("0.01"))),
            }
            for row in rows
        ]
    finally:
        storage.close()

    out_path = _base_dir() / f"report.{fmt}"
    fieldnames = ["reference", "kind", "state", "account", "amount"]

    if fmt == "json":
        out_path.write_text(json.dumps(records, indent=2) + "\n")
    else:
        delimiter = "," if fmt == "csv" else "\t"
        with out_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(records)

    print(f"wrote {out_path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from app.api import serve

    db_path = default_db_path(_base_dir())
    storage = Storage.open_existing(db_path)
    try:
        serve(storage, host=args.host, port=args.port)
    finally:
        storage.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="replay a scenario from a clean database")
    run_parser.add_argument("scenario")
    run_parser.add_argument("--world", required=True)
    run_parser.set_defaults(func=cmd_run)

    show_parser = subparsers.add_parser("show", help="show one or more requests")
    show_parser.add_argument("references", nargs="+")
    show_parser.set_defaults(func=cmd_show)

    export_parser = subparsers.add_parser("export", help="export a report")
    export_parser.add_argument("--format", required=True, choices=sorted(EXPORT_FORMATS))
    export_parser.set_defaults(func=cmd_export)

    serve_parser = subparsers.add_parser("serve", help="serve the HTTP API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AppError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

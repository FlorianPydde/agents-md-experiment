"""Command line interface: run, show, export, serve."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path

from app.db import DEFAULT_DB_PATH, Database
from app.engine import Engine
from app.errors import AppError
from app.loader import load_scenario, load_world


def cmd_run(args: argparse.Namespace) -> int:
    world = load_world(args.world)
    scenario = load_scenario(args.scenario)

    db_path = args.db or DEFAULT_DB_PATH
    db = Database(db_path)
    db.reset()
    for account in world.accounts.values():
        db.upsert_account(account.id, account.owner, account.tier, account.balance, account.frozen)
    db.commit()

    engine = Engine(db, world)
    for entry in scenario:
        if entry["action"] == "intake":
            request = entry.get("request")
            if not isinstance(request, dict):
                raise AppError("intake entry is missing a 'request' object")
            engine.intake(request)
        elif entry["action"] == "decide":
            reference = entry.get("reference")
            role = entry.get("role")
            decision = entry.get("decision")
            if not reference:
                raise AppError("decide entry is missing 'reference'")
            if not role:
                raise AppError("decide entry is missing 'role'")
            if not decision:
                raise AppError("decide entry is missing 'decision'")
            engine.decide(reference, role, decision)
        db.commit()

    print(_format_summary(db))
    db.close()
    return 0


def _format_summary(db: Database) -> str:
    lines = []
    for row in db.all_requests_in_arrival_order():
        lines.append(f"{row['reference']:<10}{row['kind']:<20}{row['state']}")
    for row in db.all_accounts():
        balance = Decimal(row["balance"])
        status = "frozen" if row["frozen"] else "active"
        lines.append(f"{row['id']:<10}{balance:>10.2f}  {status}")
    return "\n".join(lines)


def cmd_show(args: argparse.Namespace) -> int:
    db = Database(args.db or DEFAULT_DB_PATH)
    seen: set[str] = set()
    output_lines: list[str] = []
    missing: list[str] = []

    for reference in args.references:
        if reference in seen:
            continue
        seen.add(reference)
        request_row = db.get_request(reference)
        if request_row is None:
            missing.append(reference)
            continue
        output_lines.append(_format_show(db, request_row))

    db.close()

    if missing:
        for name in missing:
            print(f"error: no such request: {name}", file=sys.stderr)
        if not output_lines:
            return 1

    print("\n\n".join(output_lines))
    return 1 if missing else 0


def _format_show(db: Database, request_row) -> str:
    lines = []
    lines.append(f"Request {request_row['reference']}")
    lines.append(f"  kind: {request_row['kind']}")
    lines.append(f"  account: {request_row['account']}")
    lines.append(f"  amount: {request_row['amount']}")
    lines.append(
        f"  requester: {request_row['requester_name']} "
        f"({request_row['requester_role']}, {request_row['requester_origin']})"
    )
    lines.append(f"  state: {request_row['state']}")

    lines.append("  steps:")
    for step in db.steps_for(request_row["reference"]):
        lines.append(f"    [{step['step_index']}] {step['operation']}: {step['state']}")

    approvals = db.approvals_for(request_row["reference"])
    lines.append("  approvals:")
    if not approvals:
        lines.append("    (none)")
    for approval in approvals:
        detail = f"    step {approval['step_index']}: requires {approval['required_role']}, status {approval['status']}"
        if approval["status"] == "resolved":
            detail += f" ({approval['resolved_role']} {approval['decision']})"
        lines.append(detail)

    lines.append("  log:")
    for entry in db.log_for(request_row["reference"]):
        lines.append(f"    #{entry['seq']} {entry['event_type']} {json.dumps(entry['data'], sort_keys=True)}")

    return "\n".join(lines)


def cmd_export(args: argparse.Namespace) -> int:
    fmt = args.format
    if fmt not in ("csv", "json", "tsv"):
        raise AppError(f"unsupported export format: {fmt!r}")

    db = Database(args.db or DEFAULT_DB_PATH)
    records = []
    for row in db.all_requests_in_arrival_order():
        records.append(
            {
                "reference": row["reference"],
                "kind": row["kind"],
                "state": row["state"],
                "account": row["account"],
                "amount": row["amount"],
            }
        )
    db.close()

    out_path = Path(f"report.{fmt}")
    if fmt == "json":
        out_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    else:
        delimiter = "," if fmt == "csv" else "\t"
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["reference", "kind", "state", "account", "amount"],
                delimiter=delimiter,
            )
            writer.writeheader()
            writer.writerows(records)

    print(f"wrote {out_path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from app.api import create_app

    application = create_app(args.db or DEFAULT_DB_PATH)
    uvicorn.run(application, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("--db", help="path to the SQLite log database", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="replay a scenario from a clean database")
    p_run.add_argument("scenario")
    p_run.add_argument("--world", required=True)
    p_run.set_defaults(func=cmd_run)

    p_show = sub.add_parser("show", help="show one or more requests")
    p_show.add_argument("references", nargs="+")
    p_show.set_defaults(func=cmd_show)

    p_export = sub.add_parser("export", help="export requests to a report file")
    p_export.add_argument("--format", required=True, choices=("csv", "json", "tsv"))
    p_export.set_defaults(func=cmd_export)

    p_serve = sub.add_parser("serve", help="serve the HTTP API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

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

"""Command line interface: ``run``, ``show``, ``export``, ``serve``."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

from app import engine
from app.errors import AppError
from app.export import export_report
from app.policy import policy_rules
from app.models import MATERIALITY, OPERATIONS, format_amount
from app.storage import DEFAULT_DB_PATH, connect, load_world, reset


def _load_json_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise AppError(f"file not found: {path}")
    try:
        text = p.read_text()
    except OSError as exc:
        raise AppError(f"could not read file {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppError(f"file {path} is not valid JSON: {exc}") from exc


def cmd_run(args: argparse.Namespace) -> int:
    world = _load_json_file(args.world)
    scenario = _load_json_file(args.scenario)

    engine.validate_world(world)
    if not isinstance(scenario, dict) or "steps" not in scenario:
        raise AppError("scenario file must hold a 'steps' list")
    steps = scenario["steps"]
    if not isinstance(steps, list):
        raise AppError("scenario 'steps' must be a list")

    conn = reset(args.db)
    try:
        load_world(conn, world)
        for entry in steps:
            if "action" not in entry:
                raise AppError("scenario entry is missing required field 'action'")
            action = entry["action"]
            if action == "intake":
                if "request" not in entry:
                    raise AppError("intake entry is missing required field 'request'")
                engine.intake(conn, entry["request"])
            elif action == "decide":
                for field in ("reference", "role", "decision"):
                    if field not in entry:
                        raise AppError(f"decide entry is missing required field '{field}'")
                engine.decide(conn, entry["reference"], entry["role"], entry["decision"])
            else:
                raise AppError(f"unknown scenario action: {action!r}")
            conn.commit()

        _print_summary(conn)
    finally:
        conn.close()
    return 0


def _print_summary(conn: sqlite3.Connection) -> None:
    requests = conn.execute(
        "SELECT reference, kind, state FROM requests ORDER BY seq"
    ).fetchall()
    for row in requests:
        print(f"{row['reference']:<10}{row['kind']:<20}{row['state']}")

    accounts = conn.execute(
        "SELECT id, balance, frozen FROM accounts ORDER BY id"
    ).fetchall()
    for row in accounts:
        status = "frozen" if row["frozen"] else "active"
        balance = format_amount(Decimal(row["balance"]))
        print(f"{row['id']:<10}{balance:>10}  {status}")


def cmd_show(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    cache: dict[str, dict] = {}
    try:
        for reference in args.references:
            if reference not in cache:
                cache[reference] = _fetch_show_bundle(conn, reference)
            _print_show_bundle(cache[reference])
    finally:
        conn.close()
    return 0


def _fetch_show_bundle(conn: sqlite3.Connection, reference: str) -> dict:
    request = conn.execute(
        "SELECT * FROM requests WHERE reference = ?", (reference,)
    ).fetchone()
    if request is None:
        raise AppError(f"unknown request reference: {reference}")

    steps = conn.execute(
        "SELECT idx, operation, state FROM steps WHERE request_ref = ? ORDER BY idx",
        (reference,),
    ).fetchall()
    approvals = conn.execute(
        "SELECT step_idx, role, status, decided_by_role, decision"
        " FROM approvals WHERE request_ref = ? ORDER BY id",
        (reference,),
    ).fetchall()
    log = conn.execute(
        "SELECT seq, event_type, data FROM log WHERE request_ref = ? ORDER BY seq",
        (reference,),
    ).fetchall()

    return {
        "request": dict(request),
        "steps": [dict(r) for r in steps],
        "approvals": [dict(r) for r in approvals],
        "log": [dict(r) for r in log],
    }


def _print_show_bundle(bundle: dict) -> None:
    request = bundle["request"]
    print(f"Request {request['reference']}")
    print(f"  kind: {request['kind']}")
    print(f"  account: {request['account']}")
    print(f"  amount: {request['amount']}")
    print(
        f"  requester: {request['requester_name']}"
        f" ({request['requester_role']}, {request['requester_origin']})"
    )
    print(f"  state: {request['state']}")

    print("  steps:")
    for step in bundle["steps"]:
        print(f"    {step['idx']}. {step['operation']}: {step['state']}")

    print("  approvals:")
    if not bundle["approvals"]:
        print("    (none)")
    for approval in bundle["approvals"]:
        decided = (
            f", decided by {approval['decided_by_role']} ({approval['decision']})"
            if approval["decided_by_role"]
            else ""
        )
        print(
            f"    step {approval['step_idx']}: needs {approval['role']},"
            f" status {approval['status']}{decided}"
        )

    print("  log:")
    for entry in bundle["log"]:
        print(f"    [{entry['seq']}] {entry['event_type']}: {entry['data']}")


def cmd_export(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    try:
        path = export_report(conn, args.format)
    finally:
        conn.close()
    print(f"wrote {path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from app.api import serve

    serve(args.db, args.host, args.port)
    return 0


def cmd_operations(args: argparse.Namespace) -> int:
    for name in OPERATIONS:
        print(f"{name}: {MATERIALITY[name]}")
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    for rule in policy_rules():
        role = rule["role"] or "-"
        auto = "auto" if rule["auto"] else "approval"
        print(f"{rule['rule']}: {auto} ({role})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="replay a scenario from a clean database")
    run_parser.add_argument("scenario", help="path to scenario.json")
    run_parser.add_argument("--world", required=True, help="path to world.json")
    run_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="path to the SQLite database")
    run_parser.set_defaults(func=cmd_run)

    show_parser = subparsers.add_parser("show", help="show one or more requests")
    show_parser.add_argument("references", nargs="+", help="request references to show")
    show_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="path to the SQLite database")
    show_parser.set_defaults(func=cmd_show)

    export_parser = subparsers.add_parser("export", help="export a report of all requests")
    export_parser.add_argument(
        "--format", choices=("csv", "json", "tsv"), default="csv", help="report format"
    )
    export_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="path to the SQLite database")
    export_parser.set_defaults(func=cmd_export)

    serve_parser = subparsers.add_parser("serve", help="serve the HTTP API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="path to the SQLite database")
    serve_parser.set_defaults(func=cmd_serve)

    operations_parser = subparsers.add_parser(
        "operations", help="list the supported operations"
    )
    operations_parser.set_defaults(func=cmd_operations)

    policy_parser = subparsers.add_parser("policy", help="list the policy rules")
    policy_parser.set_defaults(func=cmd_policy)

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
    raise SystemExit(main())

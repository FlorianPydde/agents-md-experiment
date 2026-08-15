"""Command line entry point. All faults surface as a message and a non-zero exit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .engine import Engine, format_account_line, format_request_line
from .errors import AppError
from .export import ExportFormat, export
from .parsing import IntakeEntry, parse_scenario, parse_world
from .store import RequestView, Store, default_db_path
from .world import World

HERE = Path(__file__).resolve().parent.parent


def _run(scenario_path: Path, world_path: Path, db_path: Path) -> list[str]:
    accounts = parse_world(world_path)
    entries = parse_scenario(scenario_path)
    store = Store(db_path, reset=True)
    try:
        world = World(accounts)
        store.save_accounts(world.sorted_accounts())
        engine = Engine(store, world)
        for entry in entries:
            if isinstance(entry, IntakeEntry):
                engine.intake(entry.request)
            else:
                engine.decide(entry)
        store.save_accounts(world.sorted_accounts())
        lines = [
            format_request_line(r.reference, r.kind.value, r.state.value)
            for r in store.all_requests()
        ]
        lines.extend(format_account_line(a) for a in world.sorted_accounts())
        return lines
    finally:
        store.close()


def _render_view(view: RequestView) -> list[str]:
    record = view.request
    lines = [
        f"request   {record.reference}",
        f"kind      {record.kind.value}",
        f"account   {record.account}",
        f"amount    {record.amount:.2f}",
        f"requester {record.requester_name} ({record.requester_role},"
        f" {record.requester_origin.value})",
        f"state     {record.state.value}",
        "steps:",
    ]
    for step in view.steps:
        lines.append(f"  {step.position}  {step.operation.value:<20}{step.state.value}")
    lines.append("approvals:")
    if not view.approvals:
        lines.append("  none")
    for approval in view.approvals:
        resolved = approval.resolved_by.value if approval.resolved_by else "-"
        decision = approval.decision.value if approval.decision else "-"
        lines.append(
            f"  step {approval.position}  {approval.operation.value:<20}"
            f"needs {approval.required_role.value:<12}{approval.state.value:<10}"
            f"by {resolved} ({decision})"
        )
    lines.append("log:")
    for entry in view.log:
        detail = " ".join(f"{key}={value}" for key, value in entry.detail)
        lines.append(f"  {entry.kind.value:<20}{detail}")
    return lines


def _show(references: list[str], db_path: Path) -> list[str]:
    store = Store(db_path)
    try:
        lines: list[str] = []
        for index, reference in enumerate(references):
            if index:
                lines.append("")
            lines.extend(_render_view(store.view_request(reference)))
        return lines
    finally:
        store.close()


def _export(fmt: ExportFormat, db_path: Path, directory: Path) -> list[str]:
    store = Store(db_path)
    try:
        path = export(fmt, directory, store.all_requests())
        return [f"wrote {path.name}"]
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app", description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="path to the SQLite database")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="replay a scenario from a clean database")
    run_parser.add_argument("scenario", type=Path)
    run_parser.add_argument("--world", type=Path, required=True)

    show_parser = subparsers.add_parser("show", help="show one or more requests")
    show_parser.add_argument("references", nargs="+")

    export_parser = subparsers.add_parser("export", help="write a report of all requests")
    export_parser.add_argument(
        "--format", dest="fmt", choices=[f.value for f in ExportFormat], default="csv"
    )

    serve_parser = subparsers.add_parser("serve", help="serve the HTTP API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path: Path = args.db or default_db_path()
    try:
        if args.command == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(create_app(db_path), host=args.host, port=args.port)
            return 0
        if args.command == "run":
            lines = _run(args.scenario, args.world, db_path)
        elif args.command == "show":
            lines = _show(args.references, db_path)
        else:
            lines = _export(ExportFormat(args.fmt), db_path, HERE)
    except AppError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


__all__ = ["build_parser", "main"]

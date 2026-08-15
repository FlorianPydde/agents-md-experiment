"""CLI entry point: `python -m app <command> ...`"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.engine import replay
from app.errors import AppError
from app.export import EXPORTERS, FILE_NAMES, ExportFormat
from app.loader import load_scenario, load_world
from app.reports import export_records, format_run_summary
from app.store import DEFAULT_DB_NAME, Store


def _db_path() -> Path:
    return Path.cwd() / DEFAULT_DB_NAME


def cmd_run(args: argparse.Namespace) -> int:
    scenario_path = Path(args.scenario)
    world_path = Path(args.world)
    world = load_world(world_path)
    scenario = load_scenario(scenario_path)

    store = Store.create_fresh(_db_path())
    try:
        replay(store, world, scenario)
        store.commit()
        sys.stdout.write(format_run_summary(store))
    finally:
        store.close()
    return 0


def _print_request(store: Store, reference: str) -> None:
    req = store.get_request(reference)
    if req is None:
        raise AppError(f"unknown request reference: {reference!r}")

    print(f"Request {req.reference}")
    print(f"  kind: {req.kind.value}")
    print(f"  account: {req.account}")
    print(f"  amount: {req.amount:.2f}")
    print(f"  state: {req.state.value}")

    print("  steps:")
    for step in store.get_steps(reference):
        print(f"    [{step.step_index}] {step.operation.value}: {step.state.value}")

    approvals = store.get_approvals(reference)
    print("  approvals:")
    if not approvals:
        print("    (none)")
    for approval in approvals:
        resolved = f" resolved_by={approval.resolved_by_role.value}" if approval.resolved_by_role else ""
        print(
            f"    step {approval.step_index}: role={approval.required_role.value} "
            f"state={approval.state.value}{resolved}"
        )

    print("  log:")
    for entry in store.get_log(reference):
        print(f"    [{entry.seq}] {entry.kind.value} {entry.data}")


def cmd_show(args: argparse.Namespace) -> int:
    store = Store.open_existing(_db_path())
    try:
        seen: set[str] = set()
        for reference in args.references:
            if reference in seen:
                continue
            seen.add(reference)
            _print_request(store, reference)
    finally:
        store.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    try:
        fmt = ExportFormat(args.format)
    except ValueError:
        allowed = ", ".join(f.value for f in ExportFormat)
        raise AppError(f"unsupported export format: {args.format!r} (allowed: {allowed})") from None

    store = Store.open_existing(_db_path())
    try:
        records = export_records(store)
    finally:
        store.close()

    content = EXPORTERS[fmt](records)
    out_path = Path.cwd() / FILE_NAMES[fmt]
    out_path.write_text(content, encoding="utf-8")
    print(f"wrote {out_path.name}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from app.api import create_app

    store = Store.open_existing(_db_path())
    fastapi_app = create_app(store)
    uvicorn.run(fastapi_app, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app", description="Governed Service Request Runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="replay a scenario from a clean database")
    run_parser.add_argument("scenario", help="path to scenario.json")
    run_parser.add_argument("--world", required=True, help="path to world.json")
    run_parser.set_defaults(func=cmd_run)

    show_parser = subparsers.add_parser("show", help="show one or more requests")
    show_parser.add_argument("references", nargs="+", help="request reference(s) to show")
    show_parser.set_defaults(func=cmd_show)

    export_parser = subparsers.add_parser("export", help="export requests to a report file")
    export_parser.add_argument("--format", required=True, help="csv, json, or tsv")
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

"""Command line entry points: run, show, export, serve."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.domain import Decide, DomainError, Intake, Reference, RequestRecord
from app.engine import EngineError, apply_decision, intake_request
from app.export import Format, export
from app.store import Store
from app.wire import WireError, load_scenario, load_world

DB_FILENAME = "governed.db"


class CliError(DomainError):
    """Raised for command line usage problems (missing files, bad values)."""


def _db_path() -> Path:
    return Path(DB_FILENAME)


def _read_file(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CliError(f"could not read {label} file {path}: {exc}") from exc


def cmd_run(args: argparse.Namespace) -> int:
    world_bytes = _read_file(Path(args.world), "world")
    scenario_bytes = _read_file(Path(args.scenario), "scenario")
    accounts = load_world(world_bytes)
    scenario_steps = load_scenario(scenario_bytes)

    db_path = _db_path()
    if db_path.exists():
        db_path.unlink()

    with Store(db_path) as store:
        for account in accounts.values():
            store.put_account(account)

        seq = 0
        for scenario_step in scenario_steps:
            if isinstance(scenario_step, Intake):
                seq += 1
                intake_request(store, scenario_step.request, seq)
            elif isinstance(scenario_step, Decide):
                command = scenario_step.command
                apply_decision(store, command.reference, command.role, command.decision)

        _print_summary(store)
    return 0


def _print_summary(store: Store) -> None:
    for record in store.list_requests():
        request = record.request
        print(f"{request.reference:<10}{request.kind.value:<20}{record.state.value}")
    for account in store.list_accounts():
        status = "frozen" if account.frozen else "active"
        print(f"{account.id:<10}{account.balance.formatted():>10}  {status}")


def cmd_show(args: argparse.Namespace) -> int:
    db_path = _db_path()
    if not db_path.exists():
        raise CliError(f"no database found at {db_path}; run 'run' first")

    exit_code = 0
    cache: dict[Reference, RequestRecord | None] = {}
    with Store(db_path) as store:
        for reference in args.references:
            if reference not in cache:
                cache[reference] = store.get_request(reference)
            record = cache[reference]
            if record is None:
                print(f"error: unknown request reference: {reference}", file=sys.stderr)
                exit_code = 1
                continue
            _print_request_detail(store, record)
    return exit_code


def _print_request_detail(store: Store, record: RequestRecord) -> None:
    request = record.request
    print(f"Request {request.reference}")
    print(f"  kind: {request.kind.value}")
    print(f"  account: {request.account_id}")
    print(f"  amount: {request.amount.formatted()}")
    print(
        f"  requester: {request.requester.name} "
        f"({request.requester.role}, {request.requester.origin.value})"
    )
    print(f"  state: {record.state.value}")

    print("  steps:")
    for step in store.list_steps(request.reference):
        print(f"    {step.index}: {step.operation.value} - {step.status.value}")

    print("  approvals:")
    for approval in store.list_approvals(request.reference):
        if approval.resolved:
            assert approval.decision is not None
            assert approval.resolved_by_role is not None
            resolution = f"{approval.decision.value} by {approval.resolved_by_role.value}"
        else:
            resolution = "pending"
        print(
            f"    step {approval.step_index} ({approval.operation.value}) "
            f"requires {approval.required_role.value}: {resolution}"
        )

    print("  log:")
    for entry in store.list_log(request.reference):
        data = ", ".join(f"{key}={value}" for key, value in sorted(entry.data.items()))
        print(f"    [{entry.id}] {entry.entry_type.value}: {data}")


def cmd_export(args: argparse.Namespace) -> int:
    db_path = _db_path()
    if not db_path.exists():
        raise CliError(f"no database found at {db_path}; run 'run' first")

    fmt = Format(args.format)
    with Store(db_path) as store:
        records = store.list_requests()
    path = export(fmt, records, Path.cwd())
    print(f"wrote {path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from app.api import create_app

    db_path = _db_path()
    if not db_path.exists():
        raise CliError(f"no database found at {db_path}; run 'run' first")

    uvicorn.run(create_app(db_path), host=args.host, port=args.port)
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

    export_parser = subparsers.add_parser("export", help="export a report of requests")
    export_parser.add_argument("--format", required=True, choices=[f.value for f in Format])
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
    except (DomainError, WireError, EngineError, CliError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

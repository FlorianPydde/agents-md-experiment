"""Command line entry point. Untrusted argv is parsed here."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .domain import Approval, RequestRecord
from .engine import Runner
from .enums import ExportFormat
from .errors import AppError
from .export import write_report
from .store import DEFAULT_DB, Store
from .wire import DecideEntry, IntakeEntry, load_scenario, load_world

HERE = Path(__file__).resolve().parent.parent


def cmd_run(args: argparse.Namespace) -> int:
    scenario = load_scenario(Path(args.scenario))
    world = load_world(Path(args.world))
    store = Store(Path(args.db))
    store.reset()
    store.save_world(world)
    runner = Runner(store, world)
    for entry in scenario.steps:
        _apply(runner, entry)
    for record in store.all_requests():
        print(record.request.presentation(record.state))
    for account in runner.world.sorted_accounts():
        print(account.presentation())
    store.close()
    return 0


# Two scenario entry types, stable and closed: rule 22.
def _apply(runner: Runner, entry: IntakeEntry | DecideEntry) -> None:
    if isinstance(entry, IntakeEntry):
        runner.intake(entry.request.to_domain())
    else:
        runner.resolve(entry.reference, entry.role, entry.decision)


def cmd_show(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))
    for reference in args.references:
        record = store.load_request(reference)
        _print_record(store, record)
    store.close()
    return 0


def _print_record(store: Store, record: RequestRecord) -> None:
    request = record.request
    print(f"{request.reference} {request.kind} {record.state}")
    print(f"  account {request.account_id} amount {request.amount}")
    print(f"  requester {request.requester.name} ({request.requester.origin})")
    print("  steps:")
    for step in record.steps:
        print(f"    {step.index} {step.operation:<20}{step.state}")
    print("  approvals:")
    for approval in record.approvals:
        print(f"    {_approval_line(approval)}")
    print("  log:")
    for entry in store.log_for(request.reference):
        print(f"    {entry.presentation()}")


def _approval_line(approval: Approval) -> str:
    resolution = "pending" if approval.resolution is None else str(approval.resolution)
    return (
        f"step {approval.step_index} {approval.operation} "
        f"requires {approval.required_role}: {resolution}"
    )


def cmd_export(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))
    fmt = _parse_format(args.format)
    target = write_report(store.all_requests(), fmt, HERE)
    store.close()
    print(f"wrote {target.name}")
    return 0


def _parse_format(value: str) -> ExportFormat:
    try:
        return ExportFormat(value)
    except ValueError as exc:
        allowed = ", ".join(f.value for f in ExportFormat)
        raise AppError(f"unknown format {value!r}, expected one of: {allowed}") from exc


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(Path(args.db)), host=args.host, port=args.port)
    return 0


@dataclass(frozen=True)
class Command:
    handler: Callable[[argparse.Namespace], int]
    configure: Callable[[argparse.ArgumentParser], None]


def _configure_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("scenario")
    parser.add_argument("--world", default=str(HERE / "world.json"))


def _configure_show(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("references", nargs="+")


def _configure_export(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", default=ExportFormat.CSV.value)


def _configure_serve(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)


COMMANDS: dict[str, Command] = {
    "run": Command(cmd_run, _configure_run),
    "show": Command(cmd_show, _configure_show),
    "export": Command(cmd_export, _configure_export),
    "serve": Command(cmd_serve, _configure_serve),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    for name, command in COMMANDS.items():
        child = sub.add_parser(name)
        command.configure(child)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command].handler(args)
    except AppError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

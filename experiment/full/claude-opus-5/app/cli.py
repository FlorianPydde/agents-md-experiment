"""The command line. Each command is one registry entry."""

import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.domain import ServiceRequest
from app.engine import decide, intake
from app.enums import CommandName, ExportFormat
from app.errors import ServiceRequestError
from app.reports import write_report
from app.store import DEFAULT_DATABASE, Store
from app.views import render_request, request_view, summary
from app.wire import load_scenario, load_world


@dataclass(frozen=True)
class Command:
    """Everything about one command: how it is spelled, and what it does."""

    describe: str
    configure: Callable[[ArgumentParser], None]
    handle: Callable[[Namespace], int]


def configure_run(parser: ArgumentParser) -> None:
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)


def handle_run(args: Namespace) -> int:
    scenario_path: Path = args.scenario
    world_path: Path = args.world
    accounts = load_world(world_path)
    entries = load_scenario(scenario_path)
    store = Store(args.database)
    try:
        store.start_clean(accounts)
        for entry in entries:
            if isinstance(entry, ServiceRequest):
                intake(store, entry)
            else:
                decide(store, entry)
        print(summary(store))
    finally:
        store.close()
    return 0


def configure_show(parser: ArgumentParser) -> None:
    parser.add_argument("references", nargs="+")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)


def handle_show(args: Namespace) -> int:
    references: list[str] = args.references
    store = Store(args.database)

    @lru_cache(maxsize=None)
    def view(reference: str) -> str:
        return render_request(request_view(store, reference))

    try:
        for reference in references:
            print(view(reference))
    finally:
        store.close()
    return 0


def configure_export(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--format", dest="export_format", type=ExportFormat, required=True,
        choices=tuple(ExportFormat),
    )
    parser.add_argument("--into", type=Path, default=Path())
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)


def handle_export(args: Namespace) -> int:
    export_format: ExportFormat = args.export_format
    store = Store(args.database)
    try:
        destination = write_report(store.tracked_requests(), export_format, args.into)
    finally:
        store.close()
    print(f"wrote {destination}")
    return 0


def configure_serve(parser: ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)


def handle_serve(args: Namespace) -> int:
    import uvicorn

    from app.api import build_api

    uvicorn.run(build_api(args.database), host=args.host, port=args.port)
    return 0


COMMANDS: dict[CommandName, Command] = {
    CommandName.RUN: Command(
        describe="replay a scenario from a clean database and print the summary",
        configure=configure_run,
        handle=handle_run,
    ),
    CommandName.SHOW: Command(
        describe="print requests with their steps, approvals and log entries",
        configure=configure_show,
        handle=handle_show,
    ),
    CommandName.EXPORT: Command(
        describe="write one record per request in the chosen format",
        configure=configure_export,
        handle=handle_export,
    ),
    CommandName.SERVE: Command(
        describe="serve the HTTP API",
        configure=configure_serve,
        handle=handle_serve,
    ),
}

if missing := set(CommandName) - COMMANDS.keys():
    raise RuntimeError(f"No command registered for: {missing}")


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="app", description="Governed service request runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, command in COMMANDS.items():
        subparser = subparsers.add_parser(name.value, help=command.describe)
        command.configure(subparser)
        subparser.set_defaults(chosen=name)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    chosen: CommandName = args.chosen
    try:
        return COMMANDS[chosen].handle(args)
    except ServiceRequestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

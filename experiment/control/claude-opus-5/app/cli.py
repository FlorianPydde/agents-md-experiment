"""The command line: run, show, export and serve."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import export as exporter
from . import loader, reporting
from .api import serve as serve_http
from .engine import Engine
from .errors import AppError
from .loader import DecideEvent, IntakeEvent
from .store import DATABASE_NAME, Store

FOLDER = Path(__file__).resolve().parent.parent
DATABASE = FOLDER / DATABASE_NAME


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app", description="Governed service request runner")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="replay a scenario from a clean database")
    run.add_argument("scenario", type=Path)
    run.add_argument("--world", type=Path, required=True)

    show = commands.add_parser("show", help="print one or more requests in full")
    show.add_argument("references", nargs="+")

    export_command = commands.add_parser("export", help="write a report of every request")
    export_command.add_argument("--format", default="csv", choices=exporter.formats())

    serve = commands.add_parser("serve", help="serve the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    return parser


def command_run(scenario_path: Path, world_path: Path) -> int:
    accounts = loader.load_world(world_path)
    events = loader.load_scenario(scenario_path)

    with Store.fresh(DATABASE) as store:
        store.put_accounts(accounts)
        engine = Engine(store)
        for event in events:
            if isinstance(event, IntakeEvent):
                engine.intake(event.request)
            elif isinstance(event, DecideEvent):
                engine.decide(event.reference, event.role, event.decision)
        print(reporting.summary(store))
    return 0


def _require_database() -> Store:
    if not DATABASE.exists():
        raise AppError(f"no database yet: run '{Path(sys.argv[0]).name} run' first")
    return Store(DATABASE)


def command_show(references: list[str]) -> int:
    with _require_database() as store:
        lookup = reporting.Lookup(store)
        for position, reference in enumerate(references):
            if position:
                print()
            print(reporting.render(lookup.fetch(reference)))
    return 0


def command_export(fmt: str) -> int:
    with _require_database() as store:
        destination = exporter.export(store, fmt, FOLDER)
    print(f"wrote {destination.name}")
    return 0


def command_serve(host: str, port: int) -> int:
    with _require_database() as store:
        serve_http(store, host, port)
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        match arguments.command:
            case "run":
                return command_run(arguments.scenario, arguments.world)
            case "show":
                return command_show(arguments.references)
            case "export":
                return command_export(arguments.format)
            case "serve":
                return command_serve(arguments.host, arguments.port)
    except AppError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: {error.strerror or error}", file=sys.stderr)
        return 1
    return 0

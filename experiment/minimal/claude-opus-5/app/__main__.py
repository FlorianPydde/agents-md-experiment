"""Command line entry point: run, show, export, serve."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path

from .engine import run_scenario
from .errors import AppError
from .exporting import ExportFormat, export
from .reporting import DetailReader, detail_lines, summary_lines
from .store import DEFAULT_DATABASE, Store

HERE = Path(__file__).resolve().parent.parent


class Command(StrEnum):
    RUN = "run"
    SHOW = "show"
    EXPORT = "export"
    SERVE = "serve"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app", description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE,
        help="SQLite database holding the log and the state (default: ledger.db)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(Command.RUN.value, help="replay a scenario from a clean database")
    run.add_argument("scenario", type=Path)
    run.add_argument("--world", type=Path, required=True)

    show = commands.add_parser(Command.SHOW.value, help="show one or more requests in full")
    show.add_argument("references", nargs="+")

    exporter = commands.add_parser(Command.EXPORT.value, help="write a report of every request")
    exporter.add_argument(
        "--format", dest="fmt", default=ExportFormat.CSV.value, choices=[f.value for f in ExportFormat]
    )
    exporter.add_argument("--into", type=Path, default=HERE)

    serve = commands.add_parser(Command.SERVE.value, help="serve the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    return parser


def _run(arguments: argparse.Namespace) -> int:
    engine = run_scenario(arguments.scenario, arguments.world, arguments.db)
    try:
        for line in summary_lines(engine.store.records(), engine.ledger.sorted_accounts()):
            print(line)
    finally:
        engine.store.close()
    return 0


def _show(arguments: argparse.Namespace) -> int:
    with Store(arguments.db) as store:
        reader = DetailReader(store)
        for position, reference in enumerate(arguments.references):
            if position:
                print()
            for line in detail_lines(reader.detail(reference)):
                print(line)
    return 0


def _export(arguments: argparse.Namespace) -> int:
    with Store(arguments.db) as store:
        target = export(store.records(), ExportFormat(arguments.fmt), arguments.into)
    print(f"wrote {target}")
    return 0


def _serve(arguments: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(arguments.db), host=arguments.host, port=arguments.port)
    return 0


COMMANDS: dict[Command, Callable[[argparse.Namespace], int]] = {
    Command.RUN: _run,
    Command.SHOW: _show,
    Command.EXPORT: _export,
    Command.SERVE: _serve,
}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return COMMANDS[Command(arguments.command)](arguments)
    except AppError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

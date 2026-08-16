"""Command line argument parsing and dispatch for the `app` package."""

from __future__ import annotations

import argparse
import sys

from app.cmd_export import export_command
from app.cmd_run import run_command
from app.cmd_serve import serve_command
from app.cmd_show import show_command
from app.errors import AppError

DEFAULT_DB_PATH = "log.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="path to the SQLite database")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="replay a scenario from a clean database")
    run_parser.add_argument("scenario", help="path to scenario.json")
    run_parser.add_argument("--world", required=True, help="path to world.json")

    show_parser = subparsers.add_parser("show", help="show one or more requests")
    show_parser.add_argument("references", nargs="+", help="request references to show")

    export_parser = subparsers.add_parser("export", help="export requests to a report file")
    export_parser.add_argument(
        "--format", required=True, choices=("csv", "json", "tsv"), help="output format"
    )

    serve_parser = subparsers.add_parser("serve", help="serve the HTTP API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            print(run_command(args.scenario, args.world, db_path=args.db))
        elif args.command == "show":
            print(show_command(args.references, db_path=args.db))
        elif args.command == "export":
            path = export_command(args.format, db_path=args.db)
            print(f"wrote {path}")
        elif args.command == "serve":
            serve_command(host=args.host, port=args.port, db_path=args.db)
        else:  # pragma: no cover - argparse enforces valid choices
            parser.error(f"unknown command '{args.command}'")
    except AppError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0

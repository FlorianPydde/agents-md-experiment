"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from pathlib import Path

from . import reporting
from .api import load_engine
from .engine import Engine, replay
from .errors import AppError
from .store import DEFAULT_DB_NAME, Store
from .world import World, load_json

HERE = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app", description="Governed service request runner")
    parser.add_argument(
        "--db",
        type=Path,
        default=HERE / DEFAULT_DB_NAME,
        help="path to the SQLite database (default: runner.db beside the app)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="replay a scenario from a clean database")
    run.add_argument("scenario", type=Path)
    run.add_argument("--world", type=Path, required=True)

    show = sub.add_parser("show", help="show one or more requests in detail")
    show.add_argument("reference", nargs="+")

    export = sub.add_parser("export", help="write a report of every request")
    export.add_argument("--format", dest="fmt", default="csv", choices=sorted(reporting.FORMATS))
    export.add_argument("--out", type=Path, default=None)

    serve = sub.add_parser("serve", help="serve the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    return parser


def cmd_run(args: argparse.Namespace) -> int:
    world = World.from_file(args.world)
    scenario = load_json(args.scenario, "scenario")
    with Store.fresh(args.db) as store:
        engine = Engine(world, store)
        replay(engine, scenario)
        for line in reporting.summary_lines(engine.ordered_requests(), world.sorted_accounts()):
            print(line)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    engine = load_engine(args.db)

    @lru_cache(maxsize=None)
    def lookup(reference: str) -> tuple[str, ...]:
        """Cached so asking twice for the same reference does the work once."""
        request = engine.request(reference)
        return tuple(reporting.show_lines(request, engine.store.log_entries(reference)))

    try:
        blocks = [lookup(reference) for reference in args.reference]
    finally:
        engine.store.close()

    for index, block in enumerate(blocks):
        if index:
            print()
        for line in block:
            print(line)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    engine = load_engine(args.db)
    try:
        text = reporting.render_export(engine.ordered_requests(), args.fmt)
    finally:
        engine.store.close()
    out = args.out or (HERE / f"report.{args.fmt}")
    out.write_text(text, encoding="utf-8", newline="")
    print(f"wrote {out}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    if not args.db.exists():
        raise AppError(f"database not found: {args.db}; run the scenario first")
    uvicorn.run(create_app(args.db), host=args.host, port=args.port, log_level="info")
    return 0


COMMANDS = {"run": cmd_run, "show": cmd_show, "export": cmd_export, "serve": cmd_serve}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except AppError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

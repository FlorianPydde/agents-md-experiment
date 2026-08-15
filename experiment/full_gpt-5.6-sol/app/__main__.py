from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn, TypeVar

import uvicorn
from pydantic import BaseModel, ValidationError

from .api import create_app
from .domain import AppError, ExportFormat
from .engine import Runner
from .presentation import export_report, request_details
from .storage import Store
from .wire import DecideInput, IntakeInput, ScenarioInput, WorldInput

DATABASE_PATH = Path("service.db")
WireModel = TypeVar("WireModel", bound=BaseModel)


def _load_model(path: Path, model: type[WireModel]) -> WireModel:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AppError(f"file not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise AppError(f"could not read {path}: {error}") from error
    return model.model_validate(raw)


def _run(args: argparse.Namespace) -> None:
    world = _load_model(args.world, WorldInput)
    scenario = _load_model(args.scenario, ScenarioInput)
    store = Store(DATABASE_PATH)
    store.initialize(clean=True)
    store.seed_accounts(account.to_domain() for account in world.accounts)
    runner = Runner(store)

    for action in scenario.steps:
        if isinstance(action, IntakeInput):
            runner.intake(action.request.to_domain())
        elif isinstance(action, DecideInput):
            runner.decide(action.reference, action.role, action.decision)
        else:
            raise RuntimeError(f"unsupported scenario action: {type(action)}")

    lines = [
        f"{record.request.reference:<10}"
        f"{record.request.kind.value:<20}"
        f"{record.state.value}"
        for record in store.list_requests()
    ]
    lines.extend(
        f"{account.account_id:<10}"
        f"{account.balance.display():>10}  "
        f"{'frozen' if account.frozen else 'active'}"
        for account in store.list_accounts()
    )
    print("\n".join(lines))


def _show(args: argparse.Namespace) -> None:
    store = Store(DATABASE_PATH)
    store.initialize()
    cache: dict[str, dict[str, object]] = {}
    results: list[dict[str, object]] = []
    for reference in args.references:
        if reference not in cache:
            cache[reference] = request_details(store, reference)
        results.append(cache[reference])
    output: object = results[0] if len(results) == 1 else results
    print(json.dumps(output, indent=2))


def _export(args: argparse.Namespace) -> None:
    store = Store(DATABASE_PATH)
    store.initialize()
    path = export_report(store, args.format)
    print(path)


def _serve(args: argparse.Namespace) -> None:
    uvicorn.run(
        create_app(Store(DATABASE_PATH)),
        host=args.host,
        port=args.port,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="Run governed service requests under policy control.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run")
    run.add_argument("scenario", type=Path)
    run.add_argument("--world", type=Path, required=True)
    run.set_defaults(handler=_run)

    show = commands.add_parser("show")
    show.add_argument("references", nargs="+")
    show.set_defaults(handler=_show)

    export = commands.add_parser("export")
    export.add_argument(
        "--format",
        type=ExportFormat,
        choices=list(ExportFormat),
        required=True,
    )
    export.set_defaults(handler=_export)

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=_serve)
    return parser


def _exit_with_error(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    args = _parser().parse_args()
    try:
        args.handler(args)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        _exit_with_error(details)
    except AppError as error:
        _exit_with_error(str(error))
    except (OSError, ValueError) as error:
        _exit_with_error(str(error))


if __name__ == "__main__":
    main()

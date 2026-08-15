from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Callable, TextIO

from .api import serve
from .models import (
    AppError,
    Decide,
    ExportFormat,
    Intake,
    load_scenario,
    load_world,
)
from .storage import Repository, money
from .workflow import Runner


DATABASE = Path("service_requests.db")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m app")
    commands = root.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run")
    run_parser.add_argument("scenario", type=Path)
    run_parser.add_argument("--world", type=Path, required=True)

    show_parser = commands.add_parser("show")
    show_parser.add_argument("references", nargs="+")

    export_parser = commands.add_parser("export")
    export_parser.add_argument(
        "--format", type=ExportFormat, choices=list(ExportFormat), required=True
    )

    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    return root


def run_command(scenario_path: Path, world_path: Path, output: TextIO) -> None:
    world = load_world(world_path)
    scenario = load_scenario(scenario_path)
    with Repository(DATABASE) as repository:
        repository.reset(world)
        runner = Runner(repository)
        for entry in scenario:
            if isinstance(entry, Intake):
                runner.intake(entry.request)
            elif isinstance(entry, Decide):
                runner.decide(entry.reference, entry.role, entry.decision)
        for stored in repository.list_requests():
            print(
                f"{stored.request.reference:<10}"
                f"{stored.request.kind.value:<20}"
                f"{stored.state.value}",
                file=output,
            )
        for account in repository.list_accounts():
            state = "frozen" if account.frozen else "active"
            print(
                f"{account.id:<10}{money(account.balance):>10}  {state}",
                file=output,
            )


def show_command(references: list[str], output: TextIO) -> None:
    with Repository(DATABASE) as repository:
        cache = {
            reference: repository.get_request(reference)
            for reference in dict.fromkeys(references)
        }
        missing = [reference for reference, stored in cache.items() if stored is None]
        if missing:
            raise AppError(f"request not found: {missing[0]}")
        for index, reference in enumerate(dict.fromkeys(references)):
            stored = cache[reference]
            assert stored is not None
            if index:
                print(file=output)
            print(
                f"{stored.request.reference} {stored.request.kind.value} "
                f"{stored.state.value}",
                file=output,
            )
            print("Steps:", file=output)
            for step in repository.list_steps(reference):
                print(
                    f"  {step.position}. {step.operation.value}: {step.state.value}",
                    file=output,
                )
            print("Approvals:", file=output)
            approvals = repository.list_approvals(reference)
            if not approvals:
                print("  none", file=output)
            for approval in approvals:
                decision = (
                    f" by {approval.decided_by.value}: {approval.decision.value}"
                    if approval.decision is not None
                    and approval.decided_by is not None
                    else ""
                )
                print(
                    f"  step {approval.step_id}: {approval.required_role.value} "
                    f"{approval.state.value}{decision}",
                    file=output,
                )
            print("Log:", file=output)
            for event in repository.list_events(reference):
                print(
                    f"  {event.sequence}. {event.event} "
                    f"{json.dumps(event.data, separators=(',', ':'))}",
                    file=output,
                )


def _export_delimited(
    path: Path, rows: list[dict[str, str]], delimiter: str
) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=["reference", "kind", "state", "account", "amount"],
            delimiter=delimiter,
        )
        writer.writeheader()
        writer.writerows(rows)


def _export_json(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as destination:
        json.dump(rows, destination, indent=2)
        destination.write("\n")


Exporter = Callable[[Path, list[dict[str, str]]], None]
EXPORTERS: dict[ExportFormat, tuple[str, Exporter]] = {
    ExportFormat.CSV: (
        "report.csv",
        lambda path, rows: _export_delimited(path, rows, ","),
    ),
    ExportFormat.JSON: ("report.json", _export_json),
    ExportFormat.TSV: (
        "report.tsv",
        lambda path, rows: _export_delimited(path, rows, "\t"),
    ),
}


def export_command(format_: ExportFormat) -> None:
    with Repository(DATABASE) as repository:
        rows = [
            {
                "reference": stored.request.reference,
                "kind": stored.request.kind.value,
                "state": stored.state.value,
                "account": stored.request.account_id,
                "amount": money(stored.request.amount),
            }
            for stored in repository.list_requests()
        ]
    filename, exporter = EXPORTERS[format_]
    exporter(Path(filename), rows)


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.command == "run":
            run_command(arguments.scenario, arguments.world, sys.stdout)
        elif arguments.command == "show":
            show_command(arguments.references, sys.stdout)
        elif arguments.command == "export":
            export_command(arguments.format)
        elif arguments.command == "serve":
            serve(DATABASE, arguments.host, arguments.port)
    except AppError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

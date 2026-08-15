from __future__ import annotations

import argparse
import csv
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .models import (
    ApprovalDecisionInput, ApprovalRole, Decision, DecisionStepInput, DomainError, ExportFormat, IntakeStepInput, WorldInput,
)
from .service import OPERATIONS, Service, required_approval
from .storage import Store


DATABASE = Path("governed_service.sqlite3")


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DomainError(f"file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise DomainError(f"malformed JSON in {path}: {error.msg}") from error


def open_service() -> Service:
    return Service(Store(DATABASE))


def run(scenario_path: Path, world_path: Path) -> None:
    if DATABASE.exists():
        DATABASE.unlink()
    service = open_service()
    try:
        world = WorldInput.model_validate(load_json(world_path))
        for account in world.accounts:
            service.store.add_account(account.to_domain())
        raw_scenario = load_json(scenario_path)
        if not isinstance(raw_scenario, dict) or not isinstance(raw_scenario.get("steps"), list):
            raise DomainError("scenario must contain a steps list")
        for raw_step in raw_scenario["steps"]:
            if not isinstance(raw_step, dict):
                raise DomainError("scenario step must be an object")
            action = raw_step.get("action")
            if action == "intake":
                service.intake(IntakeStepInput.model_validate(raw_step).request.to_domain())
            elif action == "decide":
                decision = DecisionStepInput.model_validate(raw_step)
                service.decide(decision.reference, decision.role, decision.decision)
            else:
                raise DomainError("scenario step action must be intake or decide")
        for item in service.list_requests():
            print(f"{item['reference']:<10}{item['kind']:<20}{item['state']}")
        for account in service.store.accounts():
            activity = "frozen" if account.frozen else "active"
            print(f"{account.identifier:<10}{account.balance.text():>10}  {activity}")
    finally:
        service.store.close()


def show(references: list[str]) -> None:
    service = open_service()
    try:
        cache: dict[str, dict[str, object]] = {}
        for reference in references:
            item = cache.get(reference)
            if item is None:
                item = service.request_view(reference)
                cache[reference] = item
            print(f"{item['reference']} {item['kind']} {item['state']}")
            for step in item["steps"]:
                print(f"  step {step['position']}: {step['operation']} {step['state']}")
            for approval in item["approvals"]:
                print(f"  approval step {approval['step']}: {approval['required_role']} {approval['decision'] or 'pending'}")
            for row in service.store.logs(reference):
                print(f"  log {row['sequence']}: {row['event']} {row['data']}")
    finally:
        service.store.close()


def export(format_: ExportFormat) -> None:
    service = open_service()
    try:
        rows = service.list_requests()
        filename = Path(f"report.{format_.value}")
        fields = ["reference", "kind", "state", "account", "amount"]
        if format_ is ExportFormat.JSON:
            filename.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
            return
        delimiter = "," if format_ is ExportFormat.CSV else "\t"
        with filename.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(rows)
    finally:
        service.store.close()


def api_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status: HTTPStatus, payload: object) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _service_response(self, action: Callable[[Service], object]) -> None:
            service = open_service()
            try:
                self._respond(HTTPStatus.OK, action(service))
            except (DomainError, LookupError, ValidationError) as error:
                self._respond(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            finally:
                service.store.close()

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.rstrip("/")
            if path == "/health":
                self._respond(HTTPStatus.OK, {"status": "ok"})
            elif path == "/requests":
                self._service_response(lambda service: service.list_requests())
            elif path == "/approvals":
                self._service_response(lambda service: [
                    {"reference": row["reference"], "step": row["step_position"], "required_role": row["required_role"]}
                    for row in service.store.pending_approvals()
                ])
            elif path == "/operations":
                self._respond(HTTPStatus.OK, [{"name": name.value, "materiality": operation.materiality.value} for name, operation in OPERATIONS.items()])
            elif path == "/policy":
                self._respond(HTTPStatus.OK, [
                    "read operations run automatically", "credits at or below 100.00 run automatically",
                    "credits above 100.00 and debits require finance", "freeze and unfreeze require risk",
                ])
            elif path.startswith("/requests/") and path.endswith("/log"):
                reference = path.removeprefix("/requests/").removesuffix("/log").rstrip("/")
                self._service_response(lambda service: [
                    {"sequence": row["sequence"], "event": row["event"], "data": json.loads(row["data"])}
                    for row in service.store.logs(reference)
                ])
            elif path.startswith("/requests/"):
                self._service_response(lambda service: service.request_view(path.removeprefix("/requests/")))
            else:
                self._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.rstrip("/")
            if not path.startswith("/approvals/"):
                self._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = ApprovalDecisionInput.model_validate_json(self.rfile.read(length))
            except (ValueError, ValidationError) as error:
                self._respond(HTTPStatus.BAD_REQUEST, {"error": f"invalid decision: {error}"})
                return
            reference = path.removeprefix("/approvals/")
            def resolve(service: Service) -> object:
                service.decide(reference, body.role, body.decision)
                return service.request_view(reference)
            self._service_response(resolve)

        def log_message(self, format: str, *args: object) -> None:
            return None
    return Handler


def serve() -> None:
    with ThreadingHTTPServer(("127.0.0.1", 8000), api_handler()) as server:
        print("Serving on http://127.0.0.1:8000")
        server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("scenario", type=Path)
    run_parser.add_argument("--world", type=Path, required=True)
    show_parser = commands.add_parser("show")
    show_parser.add_argument("references", nargs="+")
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--format", dest="format_", type=ExportFormat, required=True)
    commands.add_parser("serve")
    args = parser.parse_args()
    try:
        if args.command == "run":
            run(args.scenario, args.world)
        elif args.command == "show":
            show(args.references)
        elif args.command == "export":
            export(args.format_)
        else:
            serve()
    except (DomainError, LookupError, ValidationError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

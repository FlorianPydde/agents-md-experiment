from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .core import APPROVAL_ROLES, OPERATIONS, Service, ServiceError, policy_for, read_json


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "service_log.sqlite3"


def print_summary(service: Service) -> None:
    for request in service.requests():
        print(f"{request['reference']:<10}{request['kind']:<20}{request['state']}")
    for account in service.accounts():
        status = "frozen" if account["frozen"] else "active"
        print(f"{account['id']:<10}{Decimal(account['balance']):>10.2f}  {status}")


def show(service: Service, references: list[str]) -> None:
    cache: dict[str, dict] = {}
    for reference in references:
        if reference not in cache:
            cache[reference] = service.request_data(reference)
        print(json.dumps(cache[reference], indent=2, sort_keys=True))


def export(service: Service, fmt: str) -> None:
    records = service.requests()
    destination = ROOT / f"report.{fmt}"
    if fmt == "json":
        destination.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        return
    delimiter = "," if fmt == "csv" else "\t"
    with destination.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=["reference", "kind", "state", "account", "amount"], delimiter=delimiter)
        writer.writeheader()
        writer.writerows(records)


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: HTTPStatus, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/health":
                    self._json(HTTPStatus.OK, {"status": "ok"})
                elif path == "/requests":
                    self._json(HTTPStatus.OK, service.requests())
                elif path.startswith("/requests/") and path.endswith("/log"):
                    reference = unquote(path.removeprefix("/requests/").removesuffix("/log").rstrip("/"))
                    self._json(HTTPStatus.OK, service.request_data(reference)["events"])
                elif path.startswith("/requests/"):
                    self._json(HTTPStatus.OK, service.request_data(unquote(path.removeprefix("/requests/"))))
                elif path == "/approvals/pending":
                    self._json(HTTPStatus.OK, service.pending_approvals())
                elif path == "/operations":
                    self._json(HTTPStatus.OK, [{"name": name, "materiality": materiality} for name, materiality in OPERATIONS.items()])
                elif path == "/policy":
                    self._json(HTTPStatus.OK, {"rules": [
                        "read operations auto-run", "credits at or below 100.00 auto-run",
                        "larger credits and debits require finance", "freeze changes require risk",
                    ]})
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except ServiceError as error:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(error)})

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/approvals/resolve":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                service.decide(payload["reference"], payload["role"], payload["decision"])
                self._json(HTTPStatus.OK, {"status": "resolved"})
            except (KeyError, json.JSONDecodeError, ServiceError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def log_message(self, format: str, *args: object) -> None:
            return
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("scenario")
    run_parser.add_argument("--world", required=True)
    show_parser = commands.add_parser("show")
    show_parser.add_argument("references", nargs="+")
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--format", choices=("csv", "json", "tsv"), required=True)
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    try:
        if arguments.command == "run":
            DATABASE.unlink(missing_ok=True)
            service = Service(DATABASE)
            try:
                service.load_world(read_json(Path(arguments.world)))
                service.replay(read_json(Path(arguments.scenario)))
                print_summary(service)
            finally:
                service.close()
        else:
            service = Service(DATABASE)
            if arguments.command == "show":
                try:
                    show(service, arguments.references)
                finally:
                    service.close()
            elif arguments.command == "export":
                try:
                    export(service, arguments.format)
                finally:
                    service.close()
            else:
                server = HTTPServer(("127.0.0.1", arguments.port), make_handler(service))
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                    service.close()
    except ServiceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

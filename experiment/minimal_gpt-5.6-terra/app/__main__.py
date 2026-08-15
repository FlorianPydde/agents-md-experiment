from __future__ import annotations

import argparse
import csv
import json
from enum import StrEnum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .domain import Decision, Materiality, Role
from .service import (
    AppError,
    MATERIALITY,
    OPERATION_HANDLERS,
    Store,
    load_world,
    policy,
    replay,
    serialize_request,
)


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "service_requests.sqlite3"


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    TSV = "tsv"


def _report_rows(store: Store) -> list[dict[str, str]]:
    return [
        {
            "reference": row["reference"], "kind": row["kind"], "state": row["state"],
            "account": row["account_id"], "amount": f"{float(row['amount']):.2f}",
        }
        for row in store.request_rows()
    ]


def _export_csv(rows: list[dict[str, str]], path: Path, dialect: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=["reference", "kind", "state", "account", "amount"], dialect=dialect)
        writer.writeheader()
        writer.writerows(rows)


def _export_json(rows: list[dict[str, str]], path: Path) -> None:
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def _export_tsv(rows: list[dict[str, str]], path: Path) -> None:
    _export_csv(rows, path, "excel-tab")


EXPORTERS = {
    ExportFormat.CSV: lambda rows, path: _export_csv(rows, path, "excel"),
    ExportFormat.JSON: _export_json,
    ExportFormat.TSV: _export_tsv,
}


def run_command(scenario: Path, world: Path) -> None:
    if DATABASE.exists():
        DATABASE.unlink()
    store = Store(DATABASE)
    try:
        load_world(store, world)
        replay(store, scenario)
        for row in store.request_rows():
            print(f"{row['reference']:<10}{row['kind']:<20}{row['state']}")
        for account in sorted((store.account(row["id"]) for row in store.connection.execute("SELECT id FROM accounts")), key=lambda item: item.id):
            status = "frozen" if account.frozen else "active"
            print(f"{account.id:<10}{account.balance:>10.2f}  {status}")
    finally:
        store.close()


def show_command(references: list[str]) -> None:
    store = Store(DATABASE)
    cache: dict[str, dict[str, object]] = {}
    try:
        for reference in references:
            report = cache.get(reference)
            if report is None:
                report = serialize_request(store, reference)
                report["approvals"] = [
                    dict(row) for row in store.connection.execute(
                        "SELECT step_position, required_role, decision, resolved_role FROM approvals WHERE reference = ? ORDER BY id",
                        (reference,),
                    )
                ]
                report["log"] = [
                    {"event": row["event"], "data": json.loads(row["data"])}
                    for row in store.logs(reference)
                ]
                cache[reference] = report
            print(json.dumps(report, indent=2))
    finally:
        store.close()


def export_command(format_name: str) -> None:
    try:
        format_value = ExportFormat(format_name)
    except ValueError:
        raise AppError("format must be one of: csv, json, tsv") from None
    store = Store(DATABASE)
    try:
        target = ROOT / f"report.{format_value.value}"
        EXPORTERS[format_value](_report_rows(store), target)
    finally:
        store.close()


class ApiHandler(BaseHTTPRequestHandler):
    server: "ApiServer"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _response(self, status: HTTPStatus, body: object) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._response(status, {"error": message})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._response(HTTPStatus.OK, {"status": "ok"})
            elif path == "/requests":
                self._response(HTTPStatus.OK, [_request_summary(row) for row in self.server.store.request_rows()])
            elif path == "/approvals/pending":
                self._response(HTTPStatus.OK, [dict(row) for row in self.server.store.pending_rows()])
            elif path == "/operations":
                self._response(HTTPStatus.OK, [
                    {"operation": operation.value, "materiality": MATERIALITY[operation].value}
                    for operation in OPERATION_HANDLERS
                ])
            elif path == "/policy":
                self._response(HTTPStatus.OK, {
                    "rules": [
                        "read operations run automatically",
                        "credits through 100.00 run automatically",
                        "credits above 100.00 require finance",
                        "debits require finance",
                        "freeze and unfreeze require risk",
                        "other operations run automatically",
                    ]
                })
            elif path.startswith("/requests/") and path.endswith("/log"):
                reference = unquote(path.removeprefix("/requests/").removesuffix("/log").rstrip("/"))
                self.server.store.request(reference)
                self._response(HTTPStatus.OK, [
                    {"id": row["id"], "event": row["event"], "data": json.loads(row["data"])}
                    for row in self.server.store.logs(reference)
                ])
            elif path.startswith("/requests/"):
                reference = unquote(path.removeprefix("/requests/"))
                self._response(HTTPStatus.OK, serialize_request(self.server.store, reference))
            else:
                self._error(HTTPStatus.NOT_FOUND, "endpoint not found")
        except AppError as error:
            self._error(HTTPStatus.NOT_FOUND, str(error))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/approvals/"):
            self._error(HTTPStatus.NOT_FOUND, "endpoint not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise AppError("request body must be an object")
            role = Role(value.get("role"))
            decision = Decision(value.get("decision"))
            reference = unquote(path.removeprefix("/approvals/"))
            from .service import Runner
            Runner(self.server.store).decide(reference, role, decision)
            self._response(HTTPStatus.OK, serialize_request(self.server.store, reference))
        except (json.JSONDecodeError, ValueError, AppError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))


class ApiServer(HTTPServer):
    def __init__(self, address: tuple[str, int], store: Store) -> None:
        super().__init__(address, ApiHandler)
        self.store = store


def _request_summary(row: object) -> dict[str, str]:
    return {
        "reference": row["reference"], "kind": row["kind"], "account": row["account_id"],
        "amount": row["amount"], "state": row["state"],
    }


def serve_command(port: int) -> None:
    store = Store(DATABASE)
    server = ApiServer(("127.0.0.1", port), store)
    try:
        server.serve_forever()
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("scenario", type=Path)
    run.add_argument("--world", type=Path, required=True)
    show = commands.add_parser("show")
    show.add_argument("references", nargs="+")
    export = commands.add_parser("export")
    export.add_argument("--format", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    try:
        if arguments.command == "run":
            run_command(arguments.scenario, arguments.world)
        elif arguments.command == "show":
            show_command(arguments.references)
        elif arguments.command == "export":
            export_command(arguments.format)
        else:
            serve_command(arguments.port)
    except AppError as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

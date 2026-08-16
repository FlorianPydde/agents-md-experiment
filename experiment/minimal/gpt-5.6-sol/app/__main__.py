from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .core import (
    OPERATIONS,
    POLICY_RULES,
    AppError,
    Engine,
    load_json,
)


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "service.db"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="python -m app")
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("scenario", type=Path)
    run.add_argument("--world", required=True, type=Path)
    show = commands.add_parser("show")
    show.add_argument("references", nargs="+")
    export = commands.add_parser("export")
    export.add_argument("--format", required=True, choices=("csv", "json", "tsv"))
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return result


def open_engine() -> Engine:
    if not DATABASE.exists():
        raise AppError("database does not exist; run a scenario first")
    engine = Engine(DATABASE)
    engine.initialize()
    return engine


def run_command(scenario: Path, world: Path) -> None:
    if DATABASE.exists():
        DATABASE.unlink()
    engine = Engine(DATABASE)
    try:
        engine.initialize()
        engine.load_world(load_json(world))
        engine.replay(load_json(scenario))
        print(engine.summary())
    finally:
        engine.close()


def printable_request(engine: Engine, reference: str) -> str:
    record = engine.request_record(reference)
    lines = [
        f"Request {record['reference']}",
        f"  kind: {record['kind']}",
        f"  state: {record['state']}",
        f"  account: {record['account_id']}",
        f"  amount: {Decimal(record['amount']):.2f}",
        "Steps:",
    ]
    lines.extend(
        f"  {step['position']}. {step['operation']}: {step['state']}"
        for step in record["steps"]
    )
    lines.append("Approvals:")
    lines.extend(
        f"  step {approval['step_id']}: {approval['required_role']} "
        f"{approval['decision'] or approval['status']}"
        for approval in record["approvals"]
    )
    lines.append("Log:")
    lines.extend(
        f"  {entry['sequence']}. {entry['event']} "
        f"{json.dumps(entry['data'], separators=(',', ':'))}"
        for entry in engine.log_entries(reference)
    )
    return "\n".join(lines)


def show_command(references: list[str]) -> None:
    engine = open_engine()
    try:
        cache: dict[str, str] = {}
        output: list[str] = []
        for reference in references:
            if reference not in cache:
                cache[reference] = printable_request(engine, reference)
            if cache[reference] not in output:
                output.append(cache[reference])
        print("\n\n".join(output))
    finally:
        engine.close()


def export_command(format_name: str) -> None:
    engine = open_engine()
    try:
        records = [
            {
                "reference": row["reference"],
                "kind": row["kind"],
                "state": row["state"],
                "account": row["account_id"],
                "amount": row["amount"],
            }
            for row in engine.requests()
        ]
    finally:
        engine.close()
    destination = ROOT / f"report.{format_name}"
    if format_name == "json":
        destination.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        return
    with destination.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=("reference", "kind", "state", "account", "amount"),
            dialect="excel-tab" if format_name == "tsv" else "excel",
        )
        writer.writeheader()
        writer.writerows(records)


class ApiHandler(BaseHTTPRequestHandler):
    engine_factory = staticmethod(open_engine)

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        engine = None
        try:
            if path == "/health":
                self.send_json(200, {"status": "ok"})
                return
            if path == "/operations":
                self.send_json(
                    200,
                    [{"name": name, "materiality": materiality} for name, materiality in OPERATIONS.items()],
                )
                return
            if path == "/policy":
                self.send_json(200, POLICY_RULES)
                return
            engine = self.engine_factory()
            if path == "/requests":
                self.send_json(200, engine.requests())
            elif path == "/approvals":
                self.send_json(200, engine.pending_approvals())
            elif path.startswith("/requests/"):
                parts = path.strip("/").split("/")
                reference = unquote(parts[1])
                if len(parts) == 3 and parts[2] == "log":
                    self.send_json(200, engine.log_entries(reference))
                elif len(parts) == 2:
                    self.send_json(200, engine.request_record(reference))
                else:
                    self.send_json(404, {"error": "not found"})
            else:
                self.send_json(404, {"error": "not found"})
        except AppError as exc:
            self.send_json(404, {"error": str(exc)})
        finally:
            if engine is not None:
                engine.close()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/approvals/"):
            self.send_json(404, {"error": "not found"})
            return
        engine = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise AppError("request body is too large")
            try:
                body = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AppError("request body must be valid JSON") from exc
            if not isinstance(body, dict):
                raise AppError("request body must be an object")
            role = body.get("role")
            decision = body.get("decision")
            if not isinstance(role, str) or not isinstance(decision, str):
                raise AppError("role and decision are required strings")
            reference = unquote(path.removeprefix("/approvals/"))
            if not reference or "/" in reference:
                self.send_json(404, {"error": "not found"})
                return
            engine = self.engine_factory()
            engine.decide(reference, role, decision)
            self.send_json(200, engine.request_record(reference))
        except AppError as exc:
            self.send_json(400, {"error": str(exc)})
        finally:
            if engine is not None:
                engine.close()


def serve_command(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"Serving on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "run":
            run_command(args.scenario, args.world)
        elif args.command == "show":
            show_command(args.references)
        elif args.command == "export":
            export_command(args.format)
        elif args.command == "serve":
            serve_command(args.host, args.port)
        return 0
    except (AppError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

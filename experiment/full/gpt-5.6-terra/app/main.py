"""Governed service request runner."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

KINDS = {
    "goodwill_credit": ["read_account", "apply_credit", "notify_customer"],
    "account_recovery": ["read_account", "unfreeze_account", "apply_credit", "notify_customer"],
    "collect_debt": ["read_account", "apply_debit", "notify_customer"],
}
ROLES = {"finance", "risk", "supervisor"}
OPERATIONS = {
    "read_account": "read",
    "apply_credit": "write",
    "apply_debit": "write",
    "freeze_account": "write",
    "unfreeze_account": "write",
    "notify_customer": "write",
}
POLICY_RULES = [
    "read operations run automatically",
    "apply_credit at or below 100.00 runs automatically",
    "apply_credit above 100.00 requires finance",
    "apply_debit requires finance",
    "freeze_account and unfreeze_account require risk",
    "all other operations run automatically",
]


class RunnerError(Exception):
    pass


def amount(value):
    if isinstance(value, bool):
        raise RunnerError("amount must be a decimal string")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RunnerError("amount must be a decimal string") from None
    if not result.is_finite() or result < 0:
        raise RunnerError("amount must not be negative")
    return result.quantize(Decimal("0.01"))


class Service:
    def __init__(self, database: Path):
        self.database = database
        self.db = sqlite3.connect(database)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")

    def close(self):
        self.db.close()

    def create_schema(self):
        self.db.executescript("""
            CREATE TABLE accounts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, tier TEXT NOT NULL,
              balance TEXT NOT NULL, frozen INTEGER NOT NULL);
            CREATE TABLE requests (reference TEXT PRIMARY KEY, kind TEXT NOT NULL, account TEXT NOT NULL,
              amount TEXT NOT NULL, requester_name TEXT NOT NULL, requester_role TEXT NOT NULL,
              requester_origin TEXT NOT NULL, state TEXT NOT NULL, arrival INTEGER NOT NULL);
            CREATE TABLE steps (id INTEGER PRIMARY KEY, reference TEXT NOT NULL, position INTEGER NOT NULL,
              operation TEXT NOT NULL, state TEXT NOT NULL, UNIQUE(reference, position));
            CREATE TABLE approvals (id INTEGER PRIMARY KEY, reference TEXT NOT NULL, step_id INTEGER NOT NULL,
              role TEXT NOT NULL, state TEXT NOT NULL, decision TEXT);
            CREATE TABLE log (id INTEGER PRIMARY KEY, reference TEXT NOT NULL, event TEXT NOT NULL,
              data TEXT NOT NULL, happened_at TEXT NOT NULL);
        """)
        self.db.commit()

    def log(self, reference, event, **data):
        self.db.execute(
            "INSERT INTO log(reference,event,data,happened_at) VALUES (?,?,?,?)",
            (reference, event, json.dumps(data, separators=(",", ":"), sort_keys=True),
             datetime.now(timezone.utc).isoformat()),
        )

    def load_world(self, world):
        accounts = required(world, "accounts", "world")
        if not isinstance(accounts, list):
            raise RunnerError("world accounts must be a list")
        for account in accounts:
            for key in ("id", "owner", "tier", "balance", "frozen"):
                required(account, key, "account")
            if account["tier"] not in {"standard", "premium"}:
                raise RunnerError("account tier is invalid")
            if not isinstance(account["frozen"], bool):
                raise RunnerError("account frozen must be boolean")
            self.db.execute("INSERT INTO accounts VALUES (?,?,?,?,?)", (
                account["id"], account["owner"], account["tier"], str(amount(account["balance"])),
                int(account["frozen"])))
        self.db.commit()

    def intake(self, request):
        for key in ("reference", "kind", "account", "amount", "requester"):
            required(request, key, "request")
        if request["kind"] not in KINDS:
            raise RunnerError("request kind is invalid")
        if self.db.execute("SELECT 1 FROM accounts WHERE id=?", (request["account"],)).fetchone() is None:
            raise RunnerError(f"account {request['account']} does not exist")
        requester = request["requester"]
        for key in ("name", "role", "origin"):
            required(requester, key, "requester")
        if requester["origin"] not in {"internal", "external"}:
            raise RunnerError("requester origin is invalid")
        reference = request["reference"]
        try:
            self.db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)", (
                reference, request["kind"], request["account"], str(amount(request["amount"])),
                requester["name"], requester["role"], requester["origin"], "received",
                self.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]))
        except sqlite3.IntegrityError:
            raise RunnerError(f"request {reference} already exists") from None
        self.log(reference, "request_received", kind=request["kind"], account=request["account"])
        for pos, operation in enumerate(KINDS[request["kind"]]):
            self.db.execute("INSERT INTO steps(reference,position,operation,state) VALUES (?,?,?,?)",
                            (reference, pos, operation, "pending"))
        self.log(reference, "plan_created", steps=KINDS[request["kind"]])
        self.db.commit()
        self.advance(reference)

    def required_role(self, operation, request):
        if OPERATIONS[operation] == "read":
            return None
        if operation == "apply_credit":
            return "finance" if amount(request["amount"]) > Decimal("100.00") else None
        if operation == "apply_debit":
            return "finance"
        if operation in {"freeze_account", "unfreeze_account"}:
            return "risk"
        return None

    def advance(self, reference):
        request = self.request_row(reference)
        while True:
            step = self.db.execute(
                "SELECT * FROM steps WHERE reference=? AND state='pending' ORDER BY position LIMIT 1",
                (reference,)).fetchone()
            if step is None:
                self.db.execute("UPDATE requests SET state='completed' WHERE reference=?", (reference,))
                self.log(reference, "request_completed")
                self.db.commit()
                return
            role = self.required_role(step["operation"], request)
            self.log(reference, "policy_decided", operation=step["operation"], required_role=role)
            if role:
                self.db.execute("UPDATE requests SET state='awaiting_approval' WHERE reference=?", (reference,))
                self.db.execute("INSERT INTO approvals(reference,step_id,role,state) VALUES (?,?,?,'pending')",
                                (reference, step["id"], role))
                self.log(reference, "approval_requested", operation=step["operation"], role=role)
                self.db.commit()
                return
            if not self.execute_step(request, step):
                return

    def execute_step(self, request, step):
        account = self.db.execute("SELECT * FROM accounts WHERE id=?", (request["account"],)).fetchone()
        operation = step["operation"]
        if operation != "unfreeze_account" and operation != "read_account" and account["frozen"]:
            return self.fail(request["reference"], operation, "account is frozen")
        current = amount(account["balance"])
        change = amount(request["amount"])
        if operation == "apply_debit" and current < change:
            return self.fail(request["reference"], operation, "insufficient balance")
        if operation == "apply_credit":
            self.db.execute("UPDATE accounts SET balance=? WHERE id=?", (str(current + change), account["id"]))
        elif operation == "apply_debit":
            self.db.execute("UPDATE accounts SET balance=? WHERE id=?", (str(current - change), account["id"]))
        elif operation == "freeze_account":
            self.db.execute("UPDATE accounts SET frozen=1 WHERE id=?", (account["id"],))
        elif operation == "unfreeze_account":
            self.db.execute("UPDATE accounts SET frozen=0 WHERE id=?", (account["id"],))
        data = {"operation": operation}
        if operation == "read_account":
            data["balance"] = f"{current:.2f}"
            data["tier"] = account["tier"]
            data["frozen"] = bool(account["frozen"])
        if operation == "notify_customer":
            data["message"] = ("Internal service request completed" if request["requester_origin"] == "internal"
                               else "Your service request has been processed")
        self.db.execute("UPDATE steps SET state='completed' WHERE id=?", (step["id"],))
        self.log(request["reference"], "operation_ran", **data)
        self.db.commit()
        return True

    def fail(self, reference, operation, reason):
        self.db.execute("UPDATE steps SET state='failed' WHERE reference=? AND operation=? AND state='pending'",
                        (reference, operation))
        self.db.execute("UPDATE requests SET state='failed' WHERE reference=?", (reference,))
        self.log(reference, "operation_failed", operation=operation, reason=reason)
        self.log(reference, "request_failed", reason=reason)
        self.db.commit()
        return False

    def decide(self, reference, role, decision):
        if role not in ROLES:
            raise RunnerError("approval role is invalid")
        if decision not in {"approve", "reject"}:
            raise RunnerError("decision is invalid")
        approval = self.db.execute(
            "SELECT * FROM approvals WHERE reference=? AND state='pending' ORDER BY id LIMIT 1", (reference,)
        ).fetchone()
        if approval is None:
            raise RunnerError(f"request {reference} has no pending approval")
        if approval["role"] != role:
            raise RunnerError(f"request {reference} requires approval from {approval['role']}")
        self.db.execute("UPDATE approvals SET state='resolved',decision=? WHERE id=?", (decision, approval["id"]))
        self.log(reference, "approval_resolved", role=role, decision=decision)
        if decision == "reject":
            self.db.execute("UPDATE requests SET state='rejected' WHERE reference=?", (reference,))
            self.log(reference, "request_rejected")
            self.db.commit()
            return
        self.db.execute("UPDATE steps SET state='pending' WHERE id=?", (approval["step_id"],))
        self.db.execute("UPDATE requests SET state='received' WHERE reference=?", (reference,))
        self.db.commit()
        step = self.db.execute("SELECT * FROM steps WHERE id=?", (approval["step_id"],)).fetchone()
        if self.execute_step(self.request_row(reference), step):
            self.advance(reference)

    def request_row(self, reference):
        row = self.db.execute("SELECT * FROM requests WHERE reference=?", (reference,)).fetchone()
        if row is None:
            raise RunnerError(f"request {reference} does not exist")
        return row

    def detail(self, reference):
        request = dict(self.request_row(reference))
        request["steps"] = [dict(row) for row in self.db.execute(
            "SELECT position,operation,state FROM steps WHERE reference=? ORDER BY position", (reference,))]
        request["approvals"] = [dict(row) for row in self.db.execute(
            "SELECT role,state,decision FROM approvals WHERE reference=? ORDER BY id", (reference,))]
        request["log"] = [dict(row) for row in self.db.execute(
            "SELECT id,event,data FROM log WHERE reference=? ORDER BY id", (reference,))]
        return request

    def requests(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM requests ORDER BY arrival")]

    def pending_approvals(self):
        return [dict(row) for row in self.db.execute(
            "SELECT reference,role,step_id FROM approvals WHERE state='pending' ORDER BY id")]


def required(obj, key, context):
    if not isinstance(obj, dict) or key not in obj:
        raise RunnerError(f"{context} is missing required field {key}")
    return obj[key]


def load_json(path):
    try:
        with Path(path).open() as source:
            return json.load(source)
    except FileNotFoundError:
        raise RunnerError(f"file not found: {path}") from None
    except json.JSONDecodeError as error:
        raise RunnerError(f"malformed JSON in {path}: {error.msg}") from None


def run(scenario_path, world_path, database):
    database.unlink(missing_ok=True)
    service = Service(database)
    try:
        service.create_schema()
        service.load_world(load_json(world_path))
        scenario = load_json(scenario_path)
        steps = required(scenario, "steps", "scenario")
        if not isinstance(steps, list):
            raise RunnerError("scenario steps must be a list")
        for entry in steps:
            action = required(entry, "action", "scenario entry")
            if action == "intake":
                service.intake(required(entry, "request", "intake entry"))
            elif action == "decide":
                service.decide(required(entry, "reference", "decision entry"),
                               required(entry, "role", "decision entry"),
                               required(entry, "decision", "decision entry"))
            else:
                raise RunnerError("scenario action is invalid")
        for request in service.requests():
            print(f"{request['reference']:<10}{request['kind']:<20}{request['state']}")
        for account in service.db.execute("SELECT * FROM accounts ORDER BY id"):
            print(f"{account['id']:<10}{amount(account['balance']):>10.2f}  "
                  f"{'frozen' if account['frozen'] else 'active'}")
    finally:
        service.close()


def show(references, database):
    service = Service(database)
    try:
        cache = {}
        for reference in references:
            detail = cache.setdefault(reference, service.detail(reference))
            print(json.dumps(detail, indent=2, sort_keys=True))
    finally:
        service.close()


def export(fmt, database, destination):
    service = Service(database)
    try:
        rows = [{key: row[key] for key in ("reference", "kind", "state", "account", "amount")}
                for row in service.requests()]
        target = destination / f"report.{fmt}"
        if fmt == "json":
            target.write_text(json.dumps(rows, indent=2) + "\n")
        else:
            with target.open("w", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=["reference", "kind", "state", "account", "amount"],
                                        delimiter="\t" if fmt == "tsv" else ",")
                writer.writeheader()
                writer.writerows(rows)
    finally:
        service.close()


def serve(database, port):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, value):
            encoded = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            service = Service(database)
            try:
                path = urlparse(self.path).path
                if path == "/health":
                    return self.respond(200, {"status": "ok"})
                if path == "/requests":
                    return self.respond(200, service.requests())
                if path.startswith("/requests/") and path.endswith("/log"):
                    return self.respond(200, service.detail(unquote(path.split("/")[2]))["log"])
                if path.startswith("/requests/"):
                    return self.respond(200, service.detail(unquote(path.split("/")[2])))
                if path == "/approvals/pending":
                    return self.respond(200, service.pending_approvals())
                if path == "/operations":
                    return self.respond(200, {"operations": OPERATIONS, "policy_rules": POLICY_RULES})
                self.respond(404, {"error": "not found"})
            except RunnerError as error:
                self.respond(404, {"error": str(error)})
            finally:
                service.close()

        def do_POST(self):
            if not self.path.startswith("/approvals/"):
                return self.respond(404, {"error": "not found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(size))
                reference = unquote(urlparse(self.path).path.split("/")[2])
                service = Service(database)
                try:
                    service.decide(reference, data["role"], data["decision"])
                    self.respond(200, service.detail(reference))
                finally:
                    service.close()
            except (KeyError, json.JSONDecodeError, RunnerError) as error:
                self.respond(400, {"error": str(error)})

        def log_message(self, format, *args):
            pass
    ThreadingHTTPServer(("", port), Handler).serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="service.db")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("scenario")
    run_parser.add_argument("--world", required=True)
    show_parser = commands.add_parser("show")
    show_parser.add_argument("references", nargs="+")
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--format", choices=["csv", "json", "tsv"], required=True)
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    database = Path(args.database)
    try:
        if args.command == "run":
            run(args.scenario, args.world, database)
        elif args.command == "show":
            show(args.references, database)
        elif args.command == "export":
            export(args.format, database, Path("."))
        else:
            serve(database, args.port)
    except (RunnerError, sqlite3.Error, OSError) as error:
        parser.exit(1, f"error: {error}\n")

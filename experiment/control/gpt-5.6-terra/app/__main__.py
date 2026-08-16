from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from wsgiref.simple_server import make_server
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "service.db"
KINDS = {
    "goodwill_credit": ["read_account", "apply_credit", "notify_customer"],
    "account_recovery": ["read_account", "unfreeze_account", "apply_credit", "notify_customer"],
    "collect_debt": ["read_account", "apply_debit", "notify_customer"],
}
OPERATIONS = {
    "read_account": "read", "apply_credit": "write", "apply_debit": "write",
    "freeze_account": "write", "unfreeze_account": "write", "notify_customer": "write",
}
POLICY = [
    "read operations run automatically",
    "apply_credit up to 100.00 runs automatically",
    "apply_credit above 100.00 requires finance",
    "apply_debit requires finance",
    "freeze_account and unfreeze_account require risk",
    "all other operations run automatically",
]
_server_service: Service | None = None


class UserError(Exception):
    pass


def amount(value: object) -> Decimal:
    if not isinstance(value, str):
        raise UserError("amount must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise UserError("amount is not a valid decimal") from None
    if not result.is_finite() or result < 0:
        raise UserError("amount must be non-negative")
    return result


def load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as source:
            value = json.load(source)
    except FileNotFoundError:
        raise UserError(f"file not found: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise UserError(f"malformed file {path}: {exc}") from None
    if not isinstance(value, dict):
        raise UserError(f"malformed file {path}: expected an object")
    return value


class Service:
    def __init__(self, path: Path = DB_PATH):
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self._schema()

    def close(self):
        self.db.close()

    def _schema(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, tier TEXT NOT NULL,
              balance TEXT NOT NULL, frozen INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (
              reference TEXT PRIMARY KEY, kind TEXT NOT NULL, account TEXT NOT NULL,
              amount TEXT NOT NULL, requester TEXT NOT NULL, state TEXT NOT NULL,
              arrival INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS steps (
              reference TEXT NOT NULL, position INTEGER NOT NULL, operation TEXT NOT NULL,
              state TEXT NOT NULL, PRIMARY KEY(reference, position),
              FOREIGN KEY(reference) REFERENCES requests(reference));
            CREATE TABLE IF NOT EXISTS approvals (
              id INTEGER PRIMARY KEY, reference TEXT NOT NULL, position INTEGER NOT NULL,
              role TEXT NOT NULL, state TEXT NOT NULL, decision TEXT,
              FOREIGN KEY(reference) REFERENCES requests(reference));
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY, reference TEXT NOT NULL, event TEXT NOT NULL,
              data TEXT NOT NULL, FOREIGN KEY(reference) REFERENCES requests(reference));
        """)
        self.db.commit()

    def reset(self, world: dict):
        accounts = world.get("accounts")
        if not isinstance(accounts, list):
            raise UserError("world requires accounts")
        self.db.executescript("DELETE FROM events; DELETE FROM approvals; DELETE FROM steps; DELETE FROM requests; DELETE FROM accounts;")
        for account in accounts:
            if not isinstance(account, dict) or any(key not in account for key in ("id", "owner", "tier", "balance", "frozen")):
                raise UserError("account is missing a required field")
            if account["tier"] not in ("standard", "premium") or not isinstance(account["frozen"], bool):
                raise UserError("account has an invalid tier or frozen flag")
            balance = amount(account["balance"])
            self.db.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                            (account["id"], account["owner"], account["tier"], str(balance), account["frozen"]))
        self.db.commit()

    def event(self, reference: str, event: str, **data):
        self.db.execute("INSERT INTO events(reference,event,data) VALUES (?, ?, ?)",
                        (reference, event, json.dumps(data, sort_keys=True)))

    def _request_data(self, request: dict):
        fields = ("reference", "kind", "account", "amount", "requester")
        if not isinstance(request, dict) or any(field not in request for field in fields):
            raise UserError("request is missing a required field")
        if not isinstance(request["reference"], str) or request["kind"] not in KINDS:
            raise UserError("request has an invalid reference or kind")
        if self.db.execute("SELECT 1 FROM accounts WHERE id=?", (request["account"],)).fetchone() is None:
            raise UserError(f"unknown account: {request['account']}")
        total = amount(request["amount"])
        requester = request["requester"]
        if not isinstance(requester, dict) or any(key not in requester for key in ("name", "role", "origin")):
            raise UserError("requester is missing a required field")
        if requester["origin"] not in ("internal", "external"):
            raise UserError("requester origin is invalid")
        return request["reference"], request["kind"], request["account"], total, requester

    def intake(self, request: dict):
        reference, kind, account, total, requester = self._request_data(request)
        arrival = self.db.execute("SELECT COALESCE(MAX(arrival), -1) + 1 FROM requests").fetchone()[0]
        try:
            self.db.execute("INSERT INTO requests VALUES (?, ?, ?, ?, ?, 'received', ?)",
                            (reference, kind, account, str(total), json.dumps(requester), arrival))
        except sqlite3.IntegrityError:
            raise UserError(f"duplicate reference: {reference}") from None
        self.event(reference, "request_received", kind=kind, account=account, amount=f"{total:.2f}")
        for position, operation in enumerate(KINDS[kind]):
            self.db.execute("INSERT INTO steps VALUES (?, ?, ?, 'pending')", (reference, position, operation))
        self.event(reference, "plan_created", steps=KINDS[kind])
        self._continue(reference)
        self.db.commit()

    def _required_role(self, operation: str, total: Decimal) -> str | None:
        if OPERATIONS[operation] == "read":
            return None
        if operation == "apply_credit":
            return None if total <= Decimal("100.00") else "finance"
        if operation == "apply_debit":
            return "finance"
        if operation in ("freeze_account", "unfreeze_account"):
            return "risk"
        return None

    def _continue(self, reference: str):
        request = self.db.execute("SELECT * FROM requests WHERE reference=?", (reference,)).fetchone()
        while True:
            step = self.db.execute("SELECT * FROM steps WHERE reference=? AND state='pending' ORDER BY position LIMIT 1",
                                   (reference,)).fetchone()
            if step is None:
                self.db.execute("UPDATE requests SET state='completed' WHERE reference=?", (reference,))
                self.event(reference, "request_finalized", state="completed")
                return
            required = self._required_role(step["operation"], Decimal(request["amount"]))
            self.event(reference, "policy_decided", operation=step["operation"], role=required, allowed=required is None)
            if required:
                self.db.execute("UPDATE requests SET state='awaiting_approval' WHERE reference=?", (reference,))
                self.db.execute("INSERT INTO approvals(reference,position,role,state) VALUES (?, ?, ?, 'pending')",
                                (reference, step["position"], required))
                self.event(reference, "approval_requested", operation=step["operation"], role=required)
                return
            if not self._operate(request, step):
                return

    def _operate(self, request: sqlite3.Row, step: sqlite3.Row) -> bool:
        account = self.db.execute("SELECT * FROM accounts WHERE id=?", (request["account"],)).fetchone()
        operation, total = step["operation"], Decimal(request["amount"])
        if operation != "unfreeze_account" and OPERATIONS[operation] == "write" and account["frozen"]:
            return self._fail(request["reference"], step, "account is frozen")
        if operation == "apply_debit" and Decimal(account["balance"]) < total:
            return self._fail(request["reference"], step, "insufficient balance")
        if operation == "apply_credit":
            self.db.execute("UPDATE accounts SET balance=? WHERE id=?", (str(Decimal(account["balance"]) + total), account["id"]))
        elif operation == "apply_debit":
            self.db.execute("UPDATE accounts SET balance=? WHERE id=?", (str(Decimal(account["balance"]) - total), account["id"]))
        elif operation == "freeze_account":
            self.db.execute("UPDATE accounts SET frozen=1 WHERE id=?", (account["id"],))
        elif operation == "unfreeze_account":
            self.db.execute("UPDATE accounts SET frozen=0 WHERE id=?", (account["id"],))
        elif operation == "notify_customer":
            origin = json.loads(request["requester"])["origin"]
            message = "Internal service request completed." if origin == "internal" else "Your service request has been processed."
            self.event(request["reference"], "customer_notified", template=origin, message=message)
        self.db.execute("UPDATE steps SET state='completed' WHERE reference=? AND position=?",
                        (request["reference"], step["position"]))
        self.event(request["reference"], "operation_ran", operation=operation)
        return True

    def _fail(self, reference: str, step: sqlite3.Row, reason: str) -> bool:
        self.db.execute("UPDATE steps SET state='failed' WHERE reference=? AND position=?", (reference, step["position"]))
        self.db.execute("UPDATE requests SET state='failed' WHERE reference=?", (reference,))
        self.event(reference, "operation_failed", operation=step["operation"], reason=reason)
        self.event(reference, "request_finalized", state="failed")
        return False

    def decide(self, reference: str, role: str, decision: str):
        if role not in ("finance", "risk", "supervisor") or decision not in ("approve", "reject"):
            raise UserError("invalid role or decision")
        approval = self.db.execute("SELECT * FROM approvals WHERE reference=? AND state='pending'", (reference,)).fetchone()
        if approval is None:
            raise UserError(f"request has nothing pending: {reference}")
        if approval["role"] != role:
            raise UserError(f"approval for {reference} requires {approval['role']}, not {role}")
        self.db.execute("UPDATE approvals SET state='resolved', decision=? WHERE id=?", (decision, approval["id"]))
        self.event(reference, "approval_resolved", role=role, decision=decision)
        if decision == "reject":
            self.db.execute("UPDATE requests SET state='rejected' WHERE reference=?", (reference,))
            self.event(reference, "request_finalized", state="rejected")
        else:
            step = self.db.execute("SELECT * FROM steps WHERE reference=? AND position=?", (reference, approval["position"])).fetchone()
            request = self.db.execute("SELECT * FROM requests WHERE reference=?", (reference,)).fetchone()
            if self._operate(request, step):
                self.db.execute("UPDATE requests SET state='received' WHERE reference=?", (reference,))
                self._continue(reference)
        self.db.commit()

    def request(self, reference: str) -> dict | None:
        row = self.db.execute("SELECT * FROM requests WHERE reference=?", (reference,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["requester"] = json.loads(value["requester"])
        value["steps"] = [dict(x) for x in self.db.execute("SELECT position,operation,state FROM steps WHERE reference=? ORDER BY position", (reference,))]
        value["approvals"] = [dict(x) for x in self.db.execute("SELECT position,role,state,decision FROM approvals WHERE reference=? ORDER BY id", (reference,))]
        return value

    def requests(self):
        return [self.request(row["reference"]) for row in self.db.execute("SELECT reference FROM requests ORDER BY arrival")]

    def pending(self):
        return [dict(row) for row in self.db.execute("SELECT reference,position,role FROM approvals WHERE state='pending' ORDER BY id")]

    def log(self, reference: str):
        if self.request(reference) is None:
            return None
        return [{"id": row["id"], "event": row["event"], "data": json.loads(row["data"])}
                for row in self.db.execute("SELECT * FROM events WHERE reference=? ORDER BY id", (reference,))]


def summary(service: Service):
    for request in service.requests():
        print(f"{request['reference']:<10}{request['kind']:<20}{request['state']}")
    for account in service.db.execute("SELECT * FROM accounts ORDER BY id"):
        state = "frozen" if account["frozen"] else "active"
        print(f"{account['id']:<10}{Decimal(account['balance']):>10.2f}  {state}")


def run(args):
    service = Service()
    try:
        service.reset(load_json(args.world))
        scenario = load_json(args.scenario)
        actions = scenario.get("steps")
        if not isinstance(actions, list):
            raise UserError("scenario requires steps")
        for item in actions:
            if not isinstance(item, dict) or item.get("action") not in ("intake", "decide"):
                raise UserError("scenario action is invalid")
            if item["action"] == "intake":
                service.intake(item.get("request"))
            else:
                if any(key not in item for key in ("reference", "role", "decision")):
                    raise UserError("decision is missing a required field")
                service.decide(item["reference"], item["role"], item["decision"])
        summary(service)
    finally:
        service.close()


def show(args):
    service = Service()
    try:
        cache = {}
        for reference in args.references:
            if reference not in cache:
                cache[reference] = service.request(reference)
            value = cache[reference]
            if value is None:
                raise UserError(f"request not found: {reference}")
            print(json.dumps({**value, "log": service.log(reference)}, indent=2, sort_keys=True))
    finally:
        service.close()


def export(args):
    service = Service()
    try:
        rows = [{key: request[key] for key in ("reference", "kind", "state", "account", "amount")} for request in service.requests()]
        path = ROOT / f"report.{args.format}"
        if args.format == "json":
            path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        else:
            with path.open("w", newline="", encoding="utf-8") as target:
                writer = csv.DictWriter(target, fieldnames=["reference", "kind", "state", "account", "amount"],
                                        delimiter="\t" if args.format == "tsv" else ",")
                writer.writeheader()
                writer.writerows(rows)
    finally:
        service.close()


def application(environ, start_response):
    service = _server_service
    owned_service = service is None
    if service is None:
        service = Service()
    try:
        method, path = environ["REQUEST_METHOD"], unquote(environ["PATH_INFO"])
        if method == "GET" and path == "/health":
            result, status = {"status": "ok"}, "200 OK"
        elif method == "GET" and path == "/requests":
            result, status = service.requests(), "200 OK"
        elif method == "GET" and path == "/approvals":
            result, status = service.pending(), "200 OK"
        elif method == "GET" and path == "/operations":
            result, status = {"operations": OPERATIONS, "policy": POLICY}, "200 OK"
        elif path.startswith("/requests/"):
            bits = path.split("/")
            reference = bits[2]
            if method == "GET" and len(bits) == 3:
                result = service.request(reference)
            elif method == "GET" and len(bits) == 4 and bits[3] == "log":
                result = service.log(reference)
            else:
                result, status = {"error": "not found"}, "404 Not Found"
                start_response(status, [("Content-Type", "application/json")])
                return [json.dumps(result).encode()]
            status = "200 OK" if result is not None else "404 Not Found"
            result = result if result is not None else {"error": f"request not found: {reference}"}
        elif method == "POST" and path.startswith("/approvals/"):
            try:
                content_length = int(environ.get("CONTENT_LENGTH") or "0")
            except ValueError:
                raise UserError("invalid Content-Length") from None
            if content_length == 0:
                raise UserError("approval body is required")
            if content_length > 65_536:
                raise UserError("approval body is too large")
            body = json.loads(environ["wsgi.input"].read(content_length))
            if not isinstance(body, dict):
                raise UserError("approval body must be an object")
            bits = path.strip("/").split("/")
            if len(bits) not in (2, 3) or (len(bits) == 3 and bits[2] != "decide"):
                result, status = {"error": "not found"}, "404 Not Found"
                start_response(status, [("Content-Type", "application/json")])
                return [json.dumps(result).encode()]
            service.decide(bits[1], body.get("role"), body.get("decision"))
            result, status = {"status": "resolved"}, "200 OK"
        else:
            result, status = {"error": "not found"}, "404 Not Found"
    except (UserError, json.JSONDecodeError) as exc:
        result, status = {"error": str(exc)}, "400 Bad Request"
    finally:
        if owned_service:
            service.close()
    start_response(status, [("Content-Type", "application/json")])
    return [json.dumps(result).encode()]


def serve(_args):
    global _server_service
    print("Serving on http://127.0.0.1:8000")
    _server_service = Service()
    try:
        make_server("127.0.0.1", 8000, application).serve_forever()
    finally:
        _server_service.close()
        _server_service = None


def main():
    parser = argparse.ArgumentParser(prog="python -m app")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run")
    command.add_argument("scenario")
    command.add_argument("--world", required=True)
    command.set_defaults(func=run)
    command = commands.add_parser("show")
    command.add_argument("references", nargs="+")
    command.set_defaults(func=show)
    command = commands.add_parser("export")
    command.add_argument("--format", choices=("csv", "json", "tsv"), required=True)
    command.set_defaults(func=export)
    commands.add_parser("serve").set_defaults(func=serve)
    args = parser.parse_args()
    try:
        args.func(args)
    except UserError as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()

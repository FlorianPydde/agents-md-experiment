from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
DB_NAME = "service.db"
KINDS = {
    "goodwill_credit": ["read_account", "apply_credit", "notify_customer"],
    "account_recovery": ["read_account", "unfreeze_account", "apply_credit", "notify_customer"],
    "collect_debt": ["read_account", "apply_debit", "notify_customer"],
}
OPERATIONS = {
    "read_account": "read", "apply_credit": "write", "apply_debit": "write",
    "freeze_account": "write", "unfreeze_account": "write", "notify_customer": "write",
}
ROLES = {"finance", "risk", "supervisor"}


class Error(Exception):
    pass


def amount(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Error(f"invalid amount: {value!r}") from None
    if not result.is_finite() or result < 0:
        raise Error(f"amount must be non-negative: {value!r}")
    return result.quantize(Decimal("0.01"))


def load_json(path: str | Path) -> Any:
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError:
        raise Error(f"file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise Error(f"malformed JSON in {path}: {exc.msg}") from None


def required(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise Error(f"required field missing: {key}")
    return data[key]


class Service:
    def __init__(self, database: Path):
        self.db = sqlite3.connect(database)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.setup()

    def close(self) -> None:
        self.db.close()

    def setup(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, tier TEXT NOT NULL CHECK(tier IN ('standard','premium')),
              balance TEXT NOT NULL, frozen INTEGER NOT NULL CHECK(frozen IN (0,1)));
            CREATE TABLE IF NOT EXISTS requests (
              reference TEXT PRIMARY KEY, kind TEXT NOT NULL, account TEXT NOT NULL REFERENCES accounts(id),
              amount TEXT NOT NULL, requester TEXT NOT NULL, state TEXT NOT NULL, position INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS request_steps (
              reference TEXT NOT NULL REFERENCES requests(reference), position INTEGER NOT NULL,
              operation TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(reference, position));
            CREATE TABLE IF NOT EXISTS approvals (
              id INTEGER PRIMARY KEY, reference TEXT NOT NULL REFERENCES requests(reference), position INTEGER NOT NULL,
              role TEXT NOT NULL, state TEXT NOT NULL, decision TEXT);
            CREATE TABLE IF NOT EXISTS event_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT, event TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS event_log_no_update BEFORE UPDATE ON event_log
              BEGIN SELECT RAISE(ABORT, 'event log is append only'); END;
            CREATE TRIGGER IF NOT EXISTS event_log_no_delete BEFORE DELETE ON event_log
              BEGIN SELECT RAISE(ABORT, 'event log is append only'); END;
        """)
        self.db.commit()

    def log(self, reference: str | None, event: str, **data: Any) -> None:
        self.db.execute("INSERT INTO event_log(reference,event,data) VALUES(?,?,?)",
                        (reference, event, json.dumps(data, separators=(",", ":"), sort_keys=True)))

    def seed(self, world: dict[str, Any]) -> None:
        accounts = required(world, "accounts")
        if not isinstance(accounts, list):
            raise Error("accounts must be a list")
        for account in accounts:
            for key in ("id", "owner", "tier", "balance", "frozen"):
                required(account, key)
            if account["tier"] not in {"standard", "premium"}:
                raise Error(f"invalid tier: {account['tier']}")
            if not isinstance(account["frozen"], bool):
                raise Error("frozen must be a boolean")
            balance = amount(account["balance"])
            self.db.execute("INSERT INTO accounts VALUES(?,?,?,?,?)",
                            (account["id"], account["owner"], account["tier"], f"{balance:.2f}", account["frozen"]))
        self.db.commit()

    def policy(self, operation: str, value: Decimal) -> str | None:
        if OPERATIONS[operation] == "read":
            return None
        if operation == "apply_credit":
            return None if value <= Decimal("100.00") else "finance"
        if operation == "apply_debit":
            return "finance"
        if operation in {"freeze_account", "unfreeze_account"}:
            return "risk"
        return None

    def intake(self, request: dict[str, Any]) -> None:
        for key in ("reference", "kind", "account", "amount", "requester"):
            required(request, key)
        reference, kind, account_id = request["reference"], request["kind"], request["account"]
        if kind not in KINDS:
            raise Error(f"invalid request kind: {kind}")
        requester = request["requester"]
        if not isinstance(requester, dict):
            raise Error("requester must be an object")
        for key in ("name", "role", "origin"):
            required(requester, key)
        if requester["origin"] not in {"internal", "external"}:
            raise Error(f"invalid origin: {requester['origin']}")
        value = amount(request["amount"])
        if self.db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone() is None:
            raise Error(f"unknown account: {account_id}")
        try:
            self.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,0)",
                            (reference, kind, account_id, f"{value:.2f}", json.dumps(requester), "received"))
        except sqlite3.IntegrityError:
            raise Error(f"duplicate request reference: {reference}") from None
        self.log(reference, "request_received", kind=kind, account=account_id, amount=f"{value:.2f}")
        for position, operation in enumerate(KINDS[kind]):
            self.db.execute("INSERT INTO request_steps VALUES(?,?,?,?)", (reference, position, operation, "pending"))
        self.log(reference, "plan_created", steps=KINDS[kind])
        self.advance(reference)
        self.db.commit()

    def execute(self, request: sqlite3.Row, step: sqlite3.Row) -> None:
        reference, operation = request["reference"], step["operation"]
        account = self.db.execute("SELECT * FROM accounts WHERE id=?", (request["account"],)).fetchone()
        value = Decimal(request["amount"])
        if operation != "read_account" and operation != "unfreeze_account" and account["frozen"]:
            raise Error("account is frozen")
        if operation == "apply_debit" and Decimal(account["balance"]) < value:
            raise Error("insufficient balance")
        self.log(reference, "operation_running", operation=operation)
        if operation == "apply_credit":
            self.db.execute("UPDATE accounts SET balance=? WHERE id=?", (f"{Decimal(account['balance']) + value:.2f}", account["id"]))
        elif operation == "apply_debit":
            self.db.execute("UPDATE accounts SET balance=? WHERE id=?", (f"{Decimal(account['balance']) - value:.2f}", account["id"]))
        elif operation == "freeze_account":
            self.db.execute("UPDATE accounts SET frozen=1 WHERE id=?", (account["id"],))
        elif operation == "unfreeze_account":
            self.db.execute("UPDATE accounts SET frozen=0 WHERE id=?", (account["id"],))
        elif operation == "notify_customer":
            origin = json.loads(request["requester"])["origin"]
            self.log(reference, "customer_notified", template=f"{origin}_request_complete")
        elif operation != "read_account":
            raise Error(f"unsupported operation: {operation}")
        self.db.execute("UPDATE request_steps SET state='completed' WHERE reference=? AND position=?",
                        (reference, step["position"]))

    def advance(self, reference: str) -> None:
        while True:
            request = self.get_request(reference)
            step = self.db.execute("SELECT * FROM request_steps WHERE reference=? AND position=?",
                                   (reference, request["position"])).fetchone()
            if step is None:
                self.db.execute("UPDATE requests SET state='completed' WHERE reference=?", (reference,))
                self.log(reference, "request_completed")
                return
            role = self.policy(step["operation"], Decimal(request["amount"]))
            self.log(reference, "policy_decided", operation=step["operation"], required_role=role)
            if role:
                self.db.execute("UPDATE requests SET state='awaiting_approval' WHERE reference=?", (reference,))
                self.db.execute("INSERT INTO approvals(reference,position,role,state) VALUES(?,?,?,'pending')",
                                (reference, step["position"], role))
                self.log(reference, "approval_requested", operation=step["operation"], role=role)
                return
            try:
                self.execute(request, step)
            except Error as exc:
                self.db.execute("UPDATE request_steps SET state='failed' WHERE reference=? AND position=?",
                                (reference, step["position"]))
                self.db.execute("UPDATE requests SET state='failed' WHERE reference=?", (reference,))
                self.log(reference, "operation_failed", operation=step["operation"], error=str(exc))
                self.log(reference, "request_failed")
                return
            self.db.execute("UPDATE requests SET position=position+1 WHERE reference=?", (reference,))

    def decide(self, reference: str, role: str, decision: str) -> None:
        if role not in ROLES:
            raise Error(f"invalid role: {role}")
        if decision not in {"approve", "reject"}:
            raise Error(f"invalid decision: {decision}")
        approval = self.db.execute("SELECT * FROM approvals WHERE reference=? AND state='pending'", (reference,)).fetchone()
        if approval is None:
            raise Error(f"request has nothing pending: {reference}")
        if approval["role"] != role:
            raise Error(f"approval for {reference} requires role {approval['role']}")
        self.db.execute("UPDATE approvals SET state='resolved',decision=? WHERE id=?", (decision, approval["id"]))
        self.log(reference, "approval_resolved", role=role, decision=decision)
        if decision == "reject":
            self.db.execute("UPDATE request_steps SET state='rejected' WHERE reference=? AND position=?",
                            (reference, approval["position"]))
            self.db.execute("UPDATE requests SET state='rejected' WHERE reference=?", (reference,))
            self.log(reference, "request_rejected")
        else:
            request = self.get_request(reference)
            step = self.db.execute("SELECT * FROM request_steps WHERE reference=? AND position=?",
                                   (reference, approval["position"])).fetchone()
            try:
                self.execute(request, step)
            except Error as exc:
                self.db.execute("UPDATE request_steps SET state='failed' WHERE reference=? AND position=?",
                                (reference, approval["position"]))
                self.db.execute("UPDATE requests SET state='failed' WHERE reference=?", (reference,))
                self.log(reference, "operation_failed", operation=step["operation"], error=str(exc))
                self.log(reference, "request_failed")
            else:
                self.db.execute("UPDATE requests SET state='received',position=position+1 WHERE reference=?", (reference,))
                self.advance(reference)
        self.db.commit()

    def get_request(self, reference: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM requests WHERE reference=?", (reference,)).fetchone()
        if row is None:
            raise Error(f"request not found: {reference}")
        return row

    def request_data(self, reference: str) -> dict[str, Any]:
        request = dict(self.get_request(reference))
        request["requester"] = json.loads(request["requester"])
        request["steps"] = [dict(row) for row in self.db.execute(
            "SELECT position,operation,state FROM request_steps WHERE reference=? ORDER BY position", (reference,))]
        request["approvals"] = [dict(row) for row in self.db.execute(
            "SELECT position,role,state,decision FROM approvals WHERE reference=? ORDER BY id", (reference,))]
        return request

    def events(self, reference: str) -> list[dict[str, Any]]:
        return [{"id": row["id"], "event": row["event"], "data": json.loads(row["data"])}
                for row in self.db.execute("SELECT * FROM event_log WHERE reference=? ORDER BY id", (reference,))]


def run(args: argparse.Namespace) -> int:
    db = Path(DB_NAME)
    if db.exists():
        db.unlink()
    service = Service(db)
    try:
        service.seed(load_json(args.world))
        scenario = load_json(args.scenario)
        steps = required(scenario, "steps")
        if not isinstance(steps, list):
            raise Error("steps must be a list")
        for item in steps:
            action = required(item, "action")
            if action == "intake":
                service.intake(required(item, "request"))
            elif action == "decide":
                service.decide(required(item, "reference"), required(item, "role"), required(item, "decision"))
            else:
                raise Error(f"invalid action: {action}")
        for row in service.db.execute("SELECT reference,kind,state FROM requests ORDER BY rowid"):
            print(f"{row['reference']:<10}  {row['kind']:<20} {row['state']}")
        for row in service.db.execute("SELECT id,balance,frozen FROM accounts ORDER BY id"):
            print(f"{row['id']:<10} {Decimal(row['balance']):>10.2f}  {'frozen' if row['frozen'] else 'active'}")
    finally:
        service.close()
    return 0


def show(args: argparse.Namespace) -> int:
    service = Service(Path(DB_NAME))
    try:
        cached = {reference: service.request_data(reference) for reference in dict.fromkeys(args.references)}
        for reference in args.references:
            request = cached[reference]
            print(f"{request['reference']} {request['kind']} {request['state']}")
            for step in request["steps"]:
                print(f"  step {step['position']}: {step['operation']} {step['state']}")
            for approval in request["approvals"]:
                print(f"  approval: {approval['role']} {approval['state']} {approval['decision'] or ''}".rstrip())
            for event in service.events(reference):
                print(f"  log {event['id']}: {event['event']} {json.dumps(event['data'], sort_keys=True)}")
    finally:
        service.close()
    return 0


def export(args: argparse.Namespace) -> int:
    formats = {"csv": (",", "report.csv"), "tsv": ("\t", "report.tsv")}
    service = Service(Path(DB_NAME))
    try:
        rows = [dict(row) for row in service.db.execute(
            "SELECT reference,kind,state,account,amount FROM requests ORDER BY rowid")]
        if args.format == "json":
            Path("report.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        else:
            delimiter, name = formats[args.format]
            with open(name, "w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=["reference", "kind", "state", "account", "amount"], delimiter=delimiter)
                writer.writeheader()
                writer.writerows(rows)
    finally:
        service.close()
    return 0


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, body: Any) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def service(self) -> Service:
        return Service(Path(DB_NAME))

    def do_GET(self) -> None:
        service = self.service()
        try:
            parts = self.path.strip("/").split("/")
            if self.path == "/health":
                body = {"status": "ok"}
            elif self.path == "/requests":
                body = [service.request_data(row["reference"]) for row in service.db.execute("SELECT reference FROM requests ORDER BY rowid")]
            elif self.path == "/approvals/pending":
                body = [dict(row) for row in service.db.execute("SELECT reference,position,role FROM approvals WHERE state='pending'")]
            elif len(parts) == 3 and parts[0] == "requests" and parts[2] == "log":
                body = service.events(parts[1])
            elif len(parts) == 2 and parts[0] == "requests":
                body = service.request_data(parts[1])
            elif self.path == "/operations":
                body = [{"name": key, "materiality": value} for key, value in OPERATIONS.items()]
            elif self.path == "/policy":
                body = ["read:auto", "apply_credit<=100:auto", "apply_credit>100:finance", "apply_debit:finance", "freeze/unfreeze:risk", "otherwise:auto"]
            else:
                self.respond(404, {"error": "not found"})
                return
            self.respond(200, body)
        except Error as exc:
            self.respond(404, {"error": str(exc)})
        finally:
            service.close()

    def do_POST(self) -> None:
        if not self.path.startswith("/approvals/"):
            self.respond(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length))
            service = self.service()
            try:
                service.decide(self.path.rsplit("/", 1)[-1], required(data, "role"), required(data, "decision"))
                self.respond(200, service.request_data(self.path.rsplit("/", 1)[-1]))
            finally:
                service.close()
        except (Error, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(_: argparse.Namespace) -> int:
    print("Serving on http://127.0.0.1:8000")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("scenario")
    run_parser.add_argument("--world", required=True)
    run_parser.set_defaults(func=run)
    show_parser = commands.add_parser("show")
    show_parser.add_argument("references", nargs="+")
    show_parser.set_defaults(func=show)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--format", choices=["csv", "json", "tsv"], required=True)
    export_parser.set_defaults(func=export)
    commands.add_parser("serve").set_defaults(func=serve)
    try:
        return args.func(args) if (args := parser.parse_args()) else 0
    except Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

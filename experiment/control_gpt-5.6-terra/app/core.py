from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


KINDS = {
    "goodwill_credit": ["read_account", "apply_credit", "notify_customer"],
    "account_recovery": ["read_account", "unfreeze_account", "apply_credit", "notify_customer"],
    "collect_debt": ["read_account", "apply_debit", "notify_customer"],
}
OPERATIONS = {
    "read_account": "read",
    "apply_credit": "write",
    "apply_debit": "write",
    "freeze_account": "write",
    "unfreeze_account": "write",
    "notify_customer": "write",
}
APPROVAL_ROLES = {"finance", "risk", "supervisor"}
REQUEST_STATES = {"received", "awaiting_approval", "completed", "rejected", "failed"}


class ServiceError(Exception):
    """An expected user-facing input or lifecycle error."""


def parse_amount(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ServiceError("amount must be a decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ServiceError(f"invalid amount: {value}") from error
    if not amount.is_finite() or amount < 0:
        raise ServiceError("amount must be a non-negative decimal")
    return amount


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ServiceError(f"file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ServiceError(f"malformed JSON in {path}: {error.msg}") from error


def policy_for(operation: str, amount: Decimal) -> str | None:
    if OPERATIONS[operation] == "read":
        return None
    if operation == "apply_credit":
        return None if amount <= Decimal("100.00") else "finance"
    if operation == "apply_debit":
        return "finance"
    if operation in {"freeze_account", "unfreeze_account"}:
        return "risk"
    return None


@dataclass(frozen=True)
class RequestInput:
    reference: str
    kind: str
    account: str
    amount: Decimal
    requester_name: str
    requester_role: str
    requester_origin: str


class Service:
    def __init__(self, database: Path):
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, tier TEXT NOT NULL,
                balance TEXT NOT NULL, frozen INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY, reference TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                account_id TEXT NOT NULL REFERENCES accounts(id), amount TEXT NOT NULL,
                requester_name TEXT NOT NULL, requester_role TEXT NOT NULL,
                requester_origin TEXT NOT NULL, state TEXT NOT NULL, arrival_order INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id),
                sequence INTEGER NOT NULL, operation TEXT NOT NULL, state TEXT NOT NULL,
                UNIQUE(request_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id),
                step_id INTEGER NOT NULL REFERENCES steps(id), required_role TEXT NOT NULL,
                decision TEXT, decided_role TEXT, state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id),
                event_type TEXT NOT NULL, data TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def _event(self, request_id: int, event_type: str, **data: Any) -> None:
        self.connection.execute(
            "INSERT INTO events(request_id, event_type, data) VALUES (?, ?, ?)",
            (request_id, event_type, json.dumps(data, separators=(",", ":"), sort_keys=True)),
        )

    def load_world(self, payload: Any) -> None:
        if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), list):
            raise ServiceError("world must contain an accounts list")
        for account in payload["accounts"]:
            if not isinstance(account, dict):
                raise ServiceError("account must be an object")
            required = {"id", "owner", "tier", "balance", "frozen"}
            missing = required - account.keys()
            if missing:
                raise ServiceError(f"account missing required field: {sorted(missing)[0]}")
            if account["tier"] not in {"standard", "premium"}:
                raise ServiceError(f"invalid account tier: {account['tier']}")
            if not isinstance(account["frozen"], bool):
                raise ServiceError("frozen must be a boolean")
            amount = parse_amount(account["balance"])
            self.connection.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                (account["id"], account["owner"], account["tier"], str(amount), account["frozen"]),
            )
        self.connection.commit()

    def intake(self, raw: Any) -> None:
        request = self._parse_request(raw)
        if self.connection.execute("SELECT 1 FROM accounts WHERE id = ?", (request.account,)).fetchone() is None:
            raise ServiceError(f"unknown account: {request.account}")
        arrival_order = self.connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        cursor = self.connection.execute(
            """INSERT INTO requests(reference, kind, account_id, amount, requester_name, requester_role,
               requester_origin, state, arrival_order) VALUES (?, ?, ?, ?, ?, ?, ?, 'received', ?)""",
            (request.reference, request.kind, request.account, str(request.amount), request.requester_name,
             request.requester_role, request.requester_origin, arrival_order),
        )
        request_id = cursor.lastrowid
        self._event(request_id, "request_received", reference=request.reference, kind=request.kind)
        for sequence, operation in enumerate(KINDS[request.kind]):
            self.connection.execute(
                "INSERT INTO steps(request_id, sequence, operation, state) VALUES (?, ?, ?, 'pending')",
                (request_id, sequence, operation),
            )
        self._event(request_id, "plan_created", steps=KINDS[request.kind])
        self._continue(request_id)
        self.connection.commit()

    def _parse_request(self, raw: Any) -> RequestInput:
        if not isinstance(raw, dict):
            raise ServiceError("request must be an object")
        required = {"reference", "kind", "account", "amount", "requester"}
        missing = required - raw.keys()
        if missing:
            raise ServiceError(f"request missing required field: {sorted(missing)[0]}")
        if raw["kind"] not in KINDS:
            raise ServiceError(f"invalid request kind: {raw['kind']}")
        requester = raw["requester"]
        if not isinstance(requester, dict):
            raise ServiceError("requester must be an object")
        requester_required = {"name", "role", "origin"}
        missing = requester_required - requester.keys()
        if missing:
            raise ServiceError(f"requester missing required field: {sorted(missing)[0]}")
        if requester["origin"] not in {"internal", "external"}:
            raise ServiceError(f"invalid requester origin: {requester['origin']}")
        for name in ("reference", "account"):
            if not isinstance(raw[name], str) or not raw[name]:
                raise ServiceError(f"{name} must be a non-empty string")
        return RequestInput(
            raw["reference"], raw["kind"], raw["account"], parse_amount(raw["amount"]),
            requester["name"], requester["role"], requester["origin"],
        )

    def _continue(self, request_id: int) -> None:
        while True:
            request = self._request_row(request_id)
            step = self.connection.execute(
                "SELECT * FROM steps WHERE request_id = ? AND state = 'pending' ORDER BY sequence LIMIT 1",
                (request_id,),
            ).fetchone()
            if step is None:
                self._set_final(request_id, "completed")
                return
            role = policy_for(step["operation"], Decimal(request["amount"]))
            self._event(request_id, "policy_decided", operation=step["operation"], approval_role=role)
            if role:
                self.connection.execute("UPDATE requests SET state = 'awaiting_approval' WHERE id = ?", (request_id,))
                self.connection.execute(
                    "INSERT INTO approvals(request_id, step_id, required_role, state) VALUES (?, ?, ?, 'pending')",
                    (request_id, step["id"], role),
                )
                self._event(request_id, "approval_requested", operation=step["operation"], role=role)
                return
            if not self._run_step(request, step):
                return

    def decide(self, reference: str, role: str, decision: str) -> None:
        if role not in APPROVAL_ROLES:
            raise ServiceError(f"invalid approval role: {role}")
        if decision not in {"approve", "reject"}:
            raise ServiceError(f"invalid decision: {decision}")
        request = self._request_by_reference(reference)
        approval = self.connection.execute(
            "SELECT * FROM approvals WHERE request_id = ? AND state = 'pending'", (request["id"],)
        ).fetchone()
        if approval is None:
            raise ServiceError(f"request has no pending approval: {reference}")
        if approval["required_role"] != role:
            raise ServiceError(f"approval for {reference} requires {approval['required_role']}, not {role}")
        self.connection.execute(
            "UPDATE approvals SET state = 'resolved', decision = ?, decided_role = ? WHERE id = ?",
            (decision, role, approval["id"]),
        )
        self._event(request["id"], "approval_resolved", role=role, decision=decision)
        if decision == "reject":
            self._set_final(request["id"], "rejected")
        else:
            step = self.connection.execute("SELECT * FROM steps WHERE id = ?", (approval["step_id"],)).fetchone()
            if self._run_step(request, step):
                self._continue(request["id"])
        self.connection.commit()

    def _run_step(self, request: sqlite3.Row, step: sqlite3.Row) -> bool:
        self._event(request["id"], "operation_running", operation=step["operation"])
        account = self.connection.execute("SELECT * FROM accounts WHERE id = ?", (request["account_id"],)).fetchone()
        operation = step["operation"]
        failure: str | None = None
        if account["frozen"] and OPERATIONS[operation] == "write" and operation != "unfreeze_account":
            failure = f"account is frozen: {account['id']}"
        elif operation == "apply_debit" and Decimal(account["balance"]) < Decimal(request["amount"]):
            failure = f"insufficient funds in account: {account['id']}"
        if failure:
            self.connection.execute("UPDATE steps SET state = 'failed' WHERE id = ?", (step["id"],))
            self._event(request["id"], "operation_failed", operation=operation, reason=failure)
            self._set_final(request["id"], "failed")
            return False
        if operation == "apply_credit":
            balance = Decimal(account["balance"]) + Decimal(request["amount"])
            self.connection.execute("UPDATE accounts SET balance = ? WHERE id = ?", (str(balance), account["id"]))
        elif operation == "apply_debit":
            balance = Decimal(account["balance"]) - Decimal(request["amount"])
            self.connection.execute("UPDATE accounts SET balance = ? WHERE id = ?", (str(balance), account["id"]))
        elif operation == "freeze_account":
            self.connection.execute("UPDATE accounts SET frozen = 1 WHERE id = ?", (account["id"],))
        elif operation == "unfreeze_account":
            self.connection.execute("UPDATE accounts SET frozen = 0 WHERE id = ?", (account["id"],))
        elif operation == "notify_customer":
            template = "internal_service_update" if request["requester_origin"] == "internal" else "external_service_update"
            self._event(request["id"], "customer_notified", template=template)
        self.connection.execute("UPDATE steps SET state = 'completed' WHERE id = ?", (step["id"],))
        return True

    def _set_final(self, request_id: int, state: str) -> None:
        self.connection.execute("UPDATE requests SET state = ? WHERE id = ?", (state, request_id))
        self._event(request_id, "request_finalized", state=state)

    def _request_row(self, request_id: int) -> sqlite3.Row:
        return self.connection.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()

    def _request_by_reference(self, reference: str) -> sqlite3.Row:
        request = self.connection.execute("SELECT * FROM requests WHERE reference = ?", (reference,)).fetchone()
        if request is None:
            raise ServiceError(f"request not found: {reference}")
        return request

    def replay(self, scenario: Any) -> None:
        if not isinstance(scenario, dict) or not isinstance(scenario.get("steps"), list):
            raise ServiceError("scenario must contain a steps list")
        for entry in scenario["steps"]:
            if not isinstance(entry, dict) or "action" not in entry:
                raise ServiceError("scenario entry missing required field: action")
            if entry["action"] == "intake":
                if "request" not in entry:
                    raise ServiceError("intake entry missing required field: request")
                self.intake(entry["request"])
            elif entry["action"] == "decide":
                required = {"reference", "role", "decision"}
                missing = required - entry.keys()
                if missing:
                    raise ServiceError(f"decision entry missing required field: {sorted(missing)[0]}")
                self.decide(entry["reference"], entry["role"], entry["decision"])
            else:
                raise ServiceError(f"invalid scenario action: {entry['action']}")

    def request_data(self, reference: str) -> dict[str, Any]:
        request = self._request_by_reference(reference)
        result = dict(request)
        result["steps"] = [dict(row) for row in self.connection.execute(
            "SELECT sequence, operation, state FROM steps WHERE request_id = ? ORDER BY sequence", (request["id"],)
        )]
        result["approvals"] = [dict(row) for row in self.connection.execute(
            "SELECT required_role, decision, decided_role, state FROM approvals WHERE request_id = ? ORDER BY id",
            (request["id"],),
        )]
        result["events"] = [
            {"event_type": row["event_type"], "data": json.loads(row["data"])}
            for row in self.connection.execute("SELECT event_type, data FROM events WHERE request_id = ? ORDER BY id", (request["id"],))
        ]
        return result

    def requests(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT reference, kind, state, account_id AS account, amount FROM requests ORDER BY arrival_order"
        )]

    def pending_approvals(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            """SELECT r.reference, a.required_role, s.operation FROM approvals a
               JOIN requests r ON r.id = a.request_id JOIN steps s ON s.id = a.step_id
               WHERE a.state = 'pending' ORDER BY a.id"""
        )]

    def accounts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM accounts ORDER BY id")]

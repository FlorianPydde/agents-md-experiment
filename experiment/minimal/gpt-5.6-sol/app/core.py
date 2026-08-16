from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


KINDS = {
    "goodwill_credit": ("read_account", "apply_credit", "notify_customer"),
    "account_recovery": (
        "read_account",
        "unfreeze_account",
        "apply_credit",
        "notify_customer",
    ),
    "collect_debt": ("read_account", "apply_debit", "notify_customer"),
}
OPERATIONS = {
    "read_account": "read",
    "apply_credit": "write",
    "apply_debit": "write",
    "freeze_account": "write",
    "unfreeze_account": "write",
    "notify_customer": "write",
}
POLICY_RULES = [
    {"rule": 1, "match": "read operations", "result": "automatic"},
    {"rule": 2, "match": "apply_credit amount <= 100.00", "result": "automatic"},
    {"rule": 3, "match": "apply_credit amount > 100.00", "result": "finance"},
    {"rule": 4, "match": "apply_debit", "result": "finance"},
    {"rule": 5, "match": "freeze_account or unfreeze_account", "result": "risk"},
    {"rule": 6, "match": "anything else", "result": "automatic"},
]
ROLES = {"finance", "risk", "supervisor"}
ORIGINS = {"internal", "external"}
TIERS = {"standard", "premium"}
DECISIONS = {"approve", "reject"}
MESSAGES = {
    "internal": "Your service request has been completed.",
    "external": "The requested account service has been completed.",
}


class AppError(Exception):
    pass


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError(f"{name} must be an object")
    return value


def require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise AppError(f"{name} must be a list")
    return value


def required(data: dict[str, Any], field: str, context: str) -> Any:
    if field not in data:
        raise AppError(f"{context} is missing required field '{field}'")
    return data[field]


def text(data: dict[str, Any], field: str, context: str) -> str:
    value = required(data, field, context)
    if not isinstance(value, str) or not value:
        raise AppError(f"{context}.{field} must be a non-empty string")
    return value


def choice(data: dict[str, Any], field: str, choices: set[str], context: str) -> str:
    value = text(data, field, context)
    if value not in choices:
        raise AppError(f"{context}.{field} must be one of: {', '.join(sorted(choices))}")
    return value


def amount(value: Any, context: str) -> Decimal:
    if not isinstance(value, str):
        raise AppError(f"{context} must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise AppError(f"{context} must be a valid decimal amount") from exc
    if not result.is_finite() or result < 0:
        raise AppError(f"{context} must be a non-negative decimal amount")
    return result


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError as exc:
        raise AppError(f"file not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(f"cannot read {path}: {exc}") from exc


@dataclass(frozen=True)
class RequestData:
    reference: str
    kind: str
    account: str
    amount: Decimal
    requester_name: str
    requester_role: str
    origin: str


def parse_request(raw: Any) -> RequestData:
    data = require_mapping(raw, "request")
    requester = require_mapping(required(data, "requester", "request"), "request.requester")
    return RequestData(
        reference=text(data, "reference", "request"),
        kind=choice(data, "kind", set(KINDS), "request"),
        account=text(data, "account", "request"),
        amount=amount(required(data, "amount", "request"), "request.amount"),
        requester_name=text(requester, "name", "request.requester"),
        requester_role=text(requester, "role", "request.requester"),
        origin=choice(requester, "origin", ORIGINS, "request.requester"),
    )


def policy(operation: str, arguments: dict[str, Any]) -> tuple[int, str | None]:
    if operation not in OPERATIONS:
        raise AppError(f"unsupported operation: {operation}")
    if OPERATIONS[operation] == "read":
        return 1, None
    if operation == "apply_credit":
        value = amount(arguments.get("amount"), "operation amount")
        return (2, None) if value <= Decimal("100.00") else (3, "finance")
    if operation == "apply_debit":
        return 4, "finance"
    if operation in {"freeze_account", "unfreeze_account"}:
        return 5, "risk"
    return 6, None


class Engine:
    def __init__(self, database: Path):
        self.database = database
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, tier TEXT NOT NULL,
                balance TEXT NOT NULL, frozen INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL, account_id TEXT NOT NULL, amount TEXT NOT NULL,
                requester_name TEXT NOT NULL, requester_role TEXT NOT NULL,
                origin TEXT NOT NULL, state TEXT NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(id)
            );
            CREATE TABLE IF NOT EXISTS steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL,
                position INTEGER NOT NULL, operation TEXT NOT NULL, arguments TEXT NOT NULL,
                state TEXT NOT NULL, UNIQUE(request_id, position),
                FOREIGN KEY(request_id) REFERENCES requests(id)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL,
                step_id INTEGER NOT NULL, required_role TEXT NOT NULL, status TEXT NOT NULL,
                decided_by TEXT, decision TEXT,
                FOREIGN KEY(request_id) REFERENCES requests(id),
                FOREIGN KEY(step_id) REFERENCES steps(id)
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL,
                account_id TEXT NOT NULL, message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS event_log (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL,
                event TEXT NOT NULL, data TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES requests(id)
            );
            CREATE TRIGGER IF NOT EXISTS event_log_no_update
            BEFORE UPDATE ON event_log BEGIN SELECT RAISE(ABORT, 'event log is append only'); END;
            CREATE TRIGGER IF NOT EXISTS event_log_no_delete
            BEFORE DELETE ON event_log BEGIN SELECT RAISE(ABORT, 'event log is append only'); END;
            """
        )
        self.connection.commit()

    def load_world(self, raw: Any) -> None:
        root = require_mapping(raw, "world")
        accounts = require_list(required(root, "accounts", "world"), "world.accounts")
        seen: set[str] = set()
        for index, raw_account in enumerate(accounts):
            context = f"world.accounts[{index}]"
            account = require_mapping(raw_account, context)
            account_id = text(account, "id", context)
            if account_id in seen:
                raise AppError(f"duplicate account id: {account_id}")
            seen.add(account_id)
            owner = text(account, "owner", context)
            tier = choice(account, "tier", TIERS, context)
            balance = amount(required(account, "balance", context), f"{context}.balance")
            frozen = required(account, "frozen", context)
            if not isinstance(frozen, bool):
                raise AppError(f"{context}.frozen must be a boolean")
            self.connection.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                (account_id, owner, tier, str(balance), frozen),
            )
        self.connection.commit()

    def log(self, request_id: int, event: str, data: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO event_log(request_id, event, data) VALUES (?, ?, ?)",
            (request_id, event, json.dumps(data, separators=(",", ":"))),
        )

    def intake(self, request: RequestData) -> None:
        if self.connection.execute(
            "SELECT 1 FROM requests WHERE reference = ?", (request.reference,)
        ).fetchone():
            raise AppError(f"request already exists: {request.reference}")
        if not self.connection.execute(
            "SELECT 1 FROM accounts WHERE id = ?", (request.account,)
        ).fetchone():
            raise AppError(f"unknown account: {request.account}")
        cursor = self.connection.execute(
            """
            INSERT INTO requests(
                reference, kind, account_id, amount, requester_name,
                requester_role, origin, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'received')
            """,
            (
                request.reference,
                request.kind,
                request.account,
                str(request.amount),
                request.requester_name,
                request.requester_role,
                request.origin,
            ),
        )
        request_id = int(cursor.lastrowid)
        self.log(request_id, "request_received", {"reference": request.reference})
        operations = KINDS[request.kind]
        for position, operation in enumerate(operations, 1):
            arguments: dict[str, Any] = {"account": request.account}
            if operation in {"apply_credit", "apply_debit"}:
                arguments["amount"] = str(request.amount)
            if operation == "notify_customer":
                arguments["origin"] = request.origin
            self.connection.execute(
                """
                INSERT INTO steps(request_id, position, operation, arguments, state)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (request_id, position, operation, json.dumps(arguments)),
            )
        self.log(request_id, "plan_created", {"steps": list(operations)})
        self.connection.commit()
        self._continue(request_id)

    def _continue(self, request_id: int) -> None:
        while True:
            step = self.connection.execute(
                """
                SELECT * FROM steps
                WHERE request_id = ? AND state = 'pending'
                ORDER BY position LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if step is None:
                self._finalize(request_id, "completed")
                return
            arguments = json.loads(step["arguments"])
            rule, role = policy(step["operation"], arguments)
            self.log(
                request_id,
                "policy_decided",
                {
                    "step": step["position"],
                    "operation": step["operation"],
                    "rule": rule,
                    "required_role": role,
                },
            )
            if role:
                self.connection.execute(
                    "UPDATE steps SET state = 'awaiting_approval' WHERE id = ?",
                    (step["id"],),
                )
                self.connection.execute(
                    """
                    INSERT INTO approvals(request_id, step_id, required_role, status)
                    VALUES (?, ?, ?, 'pending')
                    """,
                    (request_id, step["id"], role),
                )
                self.connection.execute(
                    "UPDATE requests SET state = 'awaiting_approval' WHERE id = ?",
                    (request_id,),
                )
                self.log(
                    request_id,
                    "approval_requested",
                    {"step": step["position"], "required_role": role},
                )
                self.connection.commit()
                return
            if not self._execute(request_id, step):
                return

    def _execute(self, request_id: int, step: sqlite3.Row) -> bool:
        arguments = json.loads(step["arguments"])
        operation = step["operation"]
        self.log(
            request_id,
            "operation_started",
            {"step": step["position"], "operation": operation},
        )
        try:
            result = self._perform(request_id, operation, arguments)
        except AppError as exc:
            self.connection.execute(
                "UPDATE steps SET state = 'failed' WHERE id = ?", (step["id"],)
            )
            self.log(
                request_id,
                "operation_failed",
                {"step": step["position"], "operation": operation, "error": str(exc)},
            )
            self._finalize(request_id, "failed")
            return False
        self.connection.execute(
            "UPDATE steps SET state = 'completed' WHERE id = ?", (step["id"],)
        )
        self.log(
            request_id,
            "operation_ran",
            {"step": step["position"], "operation": operation, "result": result},
        )
        self.connection.commit()
        return True

    def _perform(
        self, request_id: int, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if operation not in OPERATIONS:
            raise AppError(f"unsupported operation: {operation}")
        account_id = arguments.get("account")
        if not isinstance(account_id, str):
            raise AppError(f"{operation} requires an account")
        account = self.connection.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if account is None:
            raise AppError(f"unknown account: {account_id}")
        if operation == "read_account":
            return {
                "balance": account["balance"],
                "tier": account["tier"],
                "frozen": bool(account["frozen"]),
            }
        if operation != "unfreeze_account" and bool(account["frozen"]):
            raise AppError(f"account is frozen: {account_id}")
        if operation in {"apply_credit", "apply_debit"}:
            value = amount(arguments.get("amount"), f"{operation}.amount")
            balance = Decimal(account["balance"])
            if operation == "apply_debit" and balance < value:
                raise AppError(f"insufficient balance for account: {account_id}")
            new_balance = balance + value if operation == "apply_credit" else balance - value
            self.connection.execute(
                "UPDATE accounts SET balance = ? WHERE id = ?",
                (str(new_balance), account_id),
            )
            return {"balance": f"{new_balance:.2f}"}
        if operation in {"freeze_account", "unfreeze_account"}:
            frozen = operation == "freeze_account"
            self.connection.execute(
                "UPDATE accounts SET frozen = ? WHERE id = ?", (frozen, account_id)
            )
            return {"frozen": frozen}
        if operation == "notify_customer":
            origin = arguments.get("origin")
            if origin not in ORIGINS:
                raise AppError("notify_customer requires a valid origin")
            message = MESSAGES[origin]
            self.connection.execute(
                """
                INSERT INTO notifications(request_id, account_id, message)
                VALUES (?, ?, ?)
                """,
                (request_id, account_id, message),
            )
            return {"message": message}
        raise AppError(f"unsupported operation: {operation}")

    def decide(self, reference: str, role: str, decision: str) -> None:
        if role not in ROLES:
            raise AppError(f"role must be one of: {', '.join(sorted(ROLES))}")
        if decision not in DECISIONS:
            raise AppError(f"decision must be one of: {', '.join(sorted(DECISIONS))}")
        request = self.connection.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()
        if request is None:
            raise AppError(f"request not found: {reference}")
        approval = self.connection.execute(
            """
            SELECT approvals.*, steps.position, steps.operation, steps.arguments
            FROM approvals JOIN steps ON steps.id = approvals.step_id
            WHERE approvals.request_id = ? AND approvals.status = 'pending'
            """,
            (request["id"],),
        ).fetchone()
        if approval is None:
            raise AppError(f"request has no pending approval: {reference}")
        if role != approval["required_role"]:
            raise AppError(
                f"approval for {reference} requires role {approval['required_role']}, not {role}"
            )
        self.connection.execute(
            """
            UPDATE approvals SET status = 'resolved', decided_by = ?, decision = ?
            WHERE id = ?
            """,
            (role, decision, approval["id"]),
        )
        self.log(
            request["id"],
            "approval_resolved",
            {"step": approval["position"], "role": role, "decision": decision},
        )
        if decision == "reject":
            self.connection.execute(
                "UPDATE steps SET state = 'rejected' WHERE id = ?", (approval["step_id"],)
            )
            self._finalize(request["id"], "rejected")
            return
        self.connection.execute(
            "UPDATE steps SET state = 'pending' WHERE id = ?", (approval["step_id"],)
        )
        step = self.connection.execute(
            "SELECT * FROM steps WHERE id = ?", (approval["step_id"],)
        ).fetchone()
        if self._execute(request["id"], step):
            self._continue(request["id"])

    def _finalize(self, request_id: int, state: str) -> None:
        self.connection.execute(
            "UPDATE requests SET state = ? WHERE id = ?", (state, request_id)
        )
        self.log(request_id, "request_finalized", {"state": state})
        self.connection.commit()

    def replay(self, raw: Any) -> None:
        root = require_mapping(raw, "scenario")
        entries = require_list(required(root, "steps", "scenario"), "scenario.steps")
        for index, raw_entry in enumerate(entries):
            context = f"scenario.steps[{index}]"
            entry = require_mapping(raw_entry, context)
            action = choice(entry, "action", {"intake", "decide"}, context)
            if action == "intake":
                self.intake(parse_request(required(entry, "request", context)))
            else:
                self.decide(
                    text(entry, "reference", context),
                    choice(entry, "role", ROLES, context),
                    choice(entry, "decision", DECISIONS, context),
                )

    def summary(self) -> str:
        lines = [
            f"{row['reference']:<10}{row['kind']:<20}{row['state']}"
            for row in self.connection.execute("SELECT * FROM requests ORDER BY id")
        ]
        lines.extend(
            f"{row['id']:<10}{Decimal(row['balance']):>10.2f}  "
            f"{'frozen' if row['frozen'] else 'active'}"
            for row in self.connection.execute("SELECT * FROM accounts ORDER BY id")
        )
        return "\n".join(lines)

    def request_record(self, reference: str) -> dict[str, Any]:
        request = self.connection.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()
        if request is None:
            raise AppError(f"request not found: {reference}")
        result = dict(request)
        result["steps"] = [
            {**dict(row), "arguments": json.loads(row["arguments"])}
            for row in self.connection.execute(
                "SELECT * FROM steps WHERE request_id = ? ORDER BY position",
                (request["id"],),
            )
        ]
        result["approvals"] = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM approvals WHERE request_id = ? ORDER BY id",
                (request["id"],),
            )
        ]
        return result

    def requests(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM requests ORDER BY id")]

    def pending_approvals(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT requests.reference, steps.position AS step, steps.operation,
                       approvals.required_role
                FROM approvals
                JOIN requests ON requests.id = approvals.request_id
                JOIN steps ON steps.id = approvals.step_id
                WHERE approvals.status = 'pending' ORDER BY approvals.id
                """
            )
        ]

    def log_entries(self, reference: str) -> list[dict[str, Any]]:
        request = self.connection.execute(
            "SELECT id FROM requests WHERE reference = ?", (reference,)
        ).fetchone()
        if request is None:
            raise AppError(f"request not found: {reference}")
        return [
            {
                "sequence": row["sequence"],
                "event": row["event"],
                "data": json.loads(row["data"]),
            }
            for row in self.connection.execute(
                "SELECT * FROM event_log WHERE request_id = ? ORDER BY sequence",
                (request["id"],),
            )
        ]

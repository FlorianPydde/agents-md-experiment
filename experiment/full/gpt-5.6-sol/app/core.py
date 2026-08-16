from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "service_requests.sqlite3"

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
MATERIALITY = {
    "read_account": "read",
    "apply_credit": "write",
    "apply_debit": "write",
    "freeze_account": "write",
    "unfreeze_account": "write",
    "notify_customer": "write",
}
APPROVER_ROLES = {"finance", "risk", "supervisor"}
ORIGINS = {"internal", "external"}
TIERS = {"standard", "premium"}
DECISIONS = {"approve", "reject"}
POLICY_RULES = [
    {"rule": 1, "condition": "read operation", "outcome": "automatic"},
    {"rule": 2, "condition": "apply_credit amount <= 100.00", "outcome": "automatic"},
    {"rule": 3, "condition": "apply_credit amount > 100.00", "outcome": "finance"},
    {"rule": 4, "condition": "apply_debit", "outcome": "finance"},
    {"rule": 5, "condition": "freeze_account or unfreeze_account", "outcome": "risk"},
    {"rule": 6, "condition": "anything else", "outcome": "automatic"},
]
NOTIFICATIONS = {
    "internal": "Your account request has been completed.",
    "external": "The requested account service has been completed.",
}


class AppError(Exception):
    pass


class NotFoundError(AppError):
    pass


class OperationError(AppError):
    pass


def require_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError(f"{name} must be an object")
    return value


def require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise AppError(f"{name} must be a list")
    return value


def require_field(data: dict[str, Any], field: str, kind: type = str) -> Any:
    if field not in data:
        raise AppError(f"missing required field: {field}")
    value = data[field]
    if not isinstance(value, kind) or (kind is str and not value):
        raise AppError(f"{field} must be a non-empty {kind.__name__}")
    return value


def parse_amount(value: Any, field: str = "amount") -> Decimal:
    if not isinstance(value, str):
        raise AppError(f"{field} must be a decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise AppError(f"{field} must be a valid decimal") from exc
    if not amount.is_finite():
        raise AppError(f"{field} must be a finite decimal")
    if amount < 0:
        raise AppError(f"{field} must not be negative")
    if amount.as_tuple().exponent < -2:
        raise AppError(f"{field} must have at most two decimal places")
    return amount


def money(value: Decimal | str) -> str:
    return f"{Decimal(value):.2f}"


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            return require_object(json.load(handle), str(path))
    except FileNotFoundError as exc:
        raise AppError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AppError(f"malformed JSON in {path}: {exc.msg}") from exc
    except OSError as exc:
        raise AppError(f"cannot read {path}: {exc}") from exc


def policy_for(operation: str, amount: Decimal) -> str | None:
    if operation not in MATERIALITY:
        raise AppError(f"unsupported operation: {operation}")
    if MATERIALITY[operation] == "read":
        return None
    if operation == "apply_credit":
        return None if amount <= Decimal("100.00") else "finance"
    if operation == "apply_debit":
        return "finance"
    if operation in {"freeze_account", "unfreeze_account"}:
        return "risk"
    return None


class Store:
    def __init__(self, path: Path = DEFAULT_DB):
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def reset(self) -> None:
        self.connection.executescript(
            """
            DROP TABLE IF EXISTS events;
            DROP TABLE IF EXISTS approvals;
            DROP TABLE IF EXISTS steps;
            DROP TABLE IF EXISTS requests;
            DROP TABLE IF EXISTS accounts;
            """
        )
        self.initialize()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                tier TEXT NOT NULL,
                balance TEXT NOT NULL,
                frozen INTEGER NOT NULL,
                notification TEXT
            );
            CREATE TABLE IF NOT EXISTS requests (
                reference TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                account TEXT NOT NULL REFERENCES accounts(id),
                amount TEXT NOT NULL,
                requester TEXT NOT NULL,
                state TEXT NOT NULL,
                next_step INTEGER NOT NULL,
                arrival_order INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                reference TEXT NOT NULL REFERENCES requests(reference),
                position INTEGER NOT NULL,
                operation TEXT NOT NULL,
                state TEXT NOT NULL,
                PRIMARY KEY (reference, position)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reference TEXT NOT NULL REFERENCES requests(reference),
                step_position INTEGER NOT NULL,
                required_role TEXT NOT NULL,
                state TEXT NOT NULL,
                decision TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                reference TEXT NOT NULL REFERENCES requests(reference),
                event_type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TRIGGER IF NOT EXISTS events_no_update
            BEFORE UPDATE ON events BEGIN
                SELECT RAISE(ABORT, 'events are append only');
            END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete
            BEFORE DELETE ON events BEGIN
                SELECT RAISE(ABORT, 'events are append only');
            END;
            """
        )
        self.connection.commit()

    def event(self, reference: str, event_type: str, **data: Any) -> None:
        self.connection.execute(
            "INSERT INTO events(reference, event_type, data) VALUES (?, ?, ?)",
            (reference, event_type, json.dumps(data, separators=(",", ":"))),
        )

    def request_details(self, reference: str) -> dict[str, Any]:
        request = self.connection.execute(
            "SELECT reference, kind, account, amount, requester, state "
            "FROM requests WHERE reference = ?",
            (reference,),
        ).fetchone()
        if request is None:
            raise NotFoundError(f"request not found: {reference}")
        result = dict(request)
        result["requester"] = json.loads(result["requester"])
        result["steps"] = [
            dict(row)
            for row in self.connection.execute(
                "SELECT position, operation, state FROM steps "
                "WHERE reference = ? ORDER BY position",
                (reference,),
            )
        ]
        result["approvals"] = [
            dict(row)
            for row in self.connection.execute(
                "SELECT step_position, required_role, state, decision "
                "FROM approvals WHERE reference = ? ORDER BY id",
                (reference,),
            )
        ]
        result["log"] = self.log(reference)
        return result

    def log(self, reference: str) -> list[dict[str, Any]]:
        exists = self.connection.execute(
            "SELECT 1 FROM requests WHERE reference = ?", (reference,)
        ).fetchone()
        if exists is None:
            raise NotFoundError(f"request not found: {reference}")
        return [
            {
                "sequence": row["sequence"],
                "event": row["event_type"],
                "data": json.loads(row["data"]),
            }
            for row in self.connection.execute(
                "SELECT sequence, event_type, data FROM events "
                "WHERE reference = ? ORDER BY sequence",
                (reference,),
            )
        ]

    def requests(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT reference, kind, state, account, amount FROM requests "
                "ORDER BY arrival_order"
            )
        ]

    def pending_approvals(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT reference, step_position, required_role "
                "FROM approvals WHERE state = 'pending' ORDER BY id"
            )
        ]


@dataclass
class Engine:
    store: Store

    def load_world(self, document: dict[str, Any]) -> None:
        accounts = require_list(
            require_field(document, "accounts", list), "accounts"
        )
        seen: set[str] = set()
        for raw in accounts:
            account = require_object(raw, "account")
            account_id = require_field(account, "id")
            if account_id in seen:
                raise AppError(f"duplicate account id: {account_id}")
            seen.add(account_id)
            owner = require_field(account, "owner")
            tier = require_field(account, "tier")
            if tier not in TIERS:
                raise AppError(f"invalid tier for {account_id}: {tier}")
            balance = parse_amount(require_field(account, "balance"), "balance")
            frozen = require_field(account, "frozen", bool)
            self.store.connection.execute(
                "INSERT INTO accounts(id, owner, tier, balance, frozen) "
                "VALUES (?, ?, ?, ?, ?)",
                (account_id, owner, tier, money(balance), frozen),
            )
        self.store.connection.commit()

    def replay(self, document: dict[str, Any]) -> None:
        entries = require_list(require_field(document, "steps", list), "steps")
        for raw in entries:
            entry = require_object(raw, "scenario step")
            action = require_field(entry, "action")
            if action == "intake":
                self.intake(require_object(require_field(entry, "request", dict), "request"))
            elif action == "decide":
                self.decide(
                    require_field(entry, "reference"),
                    require_field(entry, "role"),
                    require_field(entry, "decision"),
                )
            else:
                raise AppError(f"invalid action: {action}")

    def intake(self, raw: dict[str, Any]) -> None:
        reference = require_field(raw, "reference")
        kind = require_field(raw, "kind")
        account = require_field(raw, "account")
        amount = parse_amount(require_field(raw, "amount"))
        requester = require_object(require_field(raw, "requester", dict), "requester")
        requester_data = {
            "name": require_field(requester, "name"),
            "role": require_field(requester, "role"),
            "origin": require_field(requester, "origin"),
        }
        if requester_data["origin"] not in ORIGINS:
            raise AppError(f"invalid requester origin: {requester_data['origin']}")
        if kind not in KINDS:
            raise AppError(f"invalid request kind: {kind}")
        if self.store.connection.execute(
            "SELECT 1 FROM accounts WHERE id = ?", (account,)
        ).fetchone() is None:
            raise AppError(f"account not found: {account}")
        if self.store.connection.execute(
            "SELECT 1 FROM requests WHERE reference = ?", (reference,)
        ).fetchone():
            raise AppError(f"duplicate request reference: {reference}")
        arrival = self.store.connection.execute(
            "SELECT COUNT(*) FROM requests"
        ).fetchone()[0]
        self.store.connection.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, ?, 'received', 0, ?)",
            (
                reference,
                kind,
                account,
                money(amount),
                json.dumps(requester_data, separators=(",", ":")),
                arrival,
            ),
        )
        self.store.event(reference, "request_received", kind=kind, account=account)
        operations = KINDS[kind]
        for position, operation in enumerate(operations):
            self.store.connection.execute(
                "INSERT INTO steps VALUES (?, ?, ?, 'pending')",
                (reference, position, operation),
            )
        self.store.event(reference, "plan_created", operations=list(operations))
        self.store.connection.commit()
        self._continue(reference)

    def decide(self, reference: str, role: str, decision: str) -> None:
        if role not in APPROVER_ROLES:
            raise AppError(f"invalid approval role: {role}")
        if decision not in DECISIONS:
            raise AppError(f"invalid decision: {decision}")
        approval = self.store.connection.execute(
            "SELECT id, step_position, required_role FROM approvals "
            "WHERE reference = ? AND state = 'pending'",
            (reference,),
        ).fetchone()
        if approval is None:
            raise AppError(f"request has no pending approval: {reference}")
        if role != approval["required_role"]:
            raise AppError(
                f"approval for {reference} requires role {approval['required_role']}, "
                f"not {role}"
            )
        self.store.connection.execute(
            "UPDATE approvals SET state = 'resolved', decision = ? WHERE id = ?",
            (decision, approval["id"]),
        )
        self.store.event(
            reference,
            "approval_resolved",
            step=approval["step_position"],
            role=role,
            decision=decision,
        )
        if decision == "reject":
            self.store.connection.execute(
                "UPDATE steps SET state = 'rejected' "
                "WHERE reference = ? AND position = ?",
                (reference, approval["step_position"]),
            )
            self._finish(reference, "rejected")
            self.store.connection.commit()
            return
        self.store.connection.execute(
            "UPDATE steps SET state = 'pending' "
            "WHERE reference = ? AND position = ?",
            (reference, approval["step_position"]),
        )
        self.store.connection.execute(
            "UPDATE requests SET state = 'received' WHERE reference = ?",
            (reference,),
        )
        self.store.connection.commit()
        if self._execute(reference, approval["step_position"]):
            self._continue(reference)

    def _continue(self, reference: str) -> None:
        request = self.store.connection.execute(
            "SELECT amount, next_step FROM requests WHERE reference = ?",
            (reference,),
        ).fetchone()
        step = self.store.connection.execute(
            "SELECT operation FROM steps WHERE reference = ? AND position = ?",
            (reference, request["next_step"]),
        ).fetchone()
        if step is None:
            self._finish(reference, "completed")
            self.store.connection.commit()
            return
        required_role = policy_for(step["operation"], Decimal(request["amount"]))
        self.store.event(
            reference,
            "policy_decided",
            step=request["next_step"],
            operation=step["operation"],
            outcome=required_role or "automatic",
        )
        if required_role:
            self.store.connection.execute(
                "UPDATE steps SET state = 'awaiting_approval' "
                "WHERE reference = ? AND position = ?",
                (reference, request["next_step"]),
            )
            self.store.connection.execute(
                "UPDATE requests SET state = 'awaiting_approval' WHERE reference = ?",
                (reference,),
            )
            self.store.connection.execute(
                "INSERT INTO approvals(reference, step_position, required_role, state) "
                "VALUES (?, ?, ?, 'pending')",
                (reference, request["next_step"], required_role),
            )
            self.store.event(
                reference,
                "approval_requested",
                step=request["next_step"],
                role=required_role,
            )
            self.store.connection.commit()
            return
        self.store.connection.commit()
        if self._execute(reference, request["next_step"]):
            self._continue(reference)

    def _execute(self, reference: str, position: int) -> bool:
        row = self.store.connection.execute(
            "SELECT r.account, r.amount, r.requester, s.operation "
            "FROM requests r JOIN steps s ON s.reference = r.reference "
            "WHERE r.reference = ? AND s.position = ?",
            (reference, position),
        ).fetchone()
        operation = row["operation"]
        self.store.event(
            reference, "operation_running", step=position, operation=operation
        )
        try:
            self._apply_operation(
                operation,
                row["account"],
                parse_amount(row["amount"]),
                json.loads(row["requester"])["origin"],
            )
        except OperationError as exc:
            self.store.event(
                reference,
                "operation_failed",
                step=position,
                operation=operation,
                reason=str(exc),
            )
            self.store.connection.execute(
                "UPDATE steps SET state = 'failed' "
                "WHERE reference = ? AND position = ?",
                (reference, position),
            )
            self._finish(reference, "failed")
            self.store.connection.commit()
            return False
        self.store.connection.execute(
            "UPDATE steps SET state = 'completed' "
            "WHERE reference = ? AND position = ?",
            (reference, position),
        )
        self.store.connection.execute(
            "UPDATE requests SET next_step = ? WHERE reference = ?",
            (position + 1, reference),
        )
        self.store.connection.commit()
        return True

    def _apply_operation(
        self, operation: str, account_id: str, amount: Decimal, origin: str
    ) -> None:
        if operation not in MATERIALITY:
            raise OperationError(f"unsupported operation: {operation}")
        account = self.store.connection.execute(
            "SELECT balance, tier, frozen FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if account is None:
            raise OperationError(f"account not found: {account_id}")
        if origin not in NOTIFICATIONS:
            raise OperationError(f"invalid notification origin: {origin}")
        if MATERIALITY[operation] == "write" and operation != "unfreeze_account":
            if account["frozen"]:
                raise OperationError(f"account is frozen: {account_id}")
        balance = Decimal(account["balance"])
        if operation == "read_account":
            return
        if operation == "apply_credit":
            self._set_balance(account_id, balance + amount)
        elif operation == "apply_debit":
            if balance < amount:
                raise OperationError(f"insufficient balance: {account_id}")
            self._set_balance(account_id, balance - amount)
        elif operation == "freeze_account":
            self.store.connection.execute(
                "UPDATE accounts SET frozen = 1 WHERE id = ?", (account_id,)
            )
        elif operation == "unfreeze_account":
            self.store.connection.execute(
                "UPDATE accounts SET frozen = 0 WHERE id = ?", (account_id,)
            )
        elif operation == "notify_customer":
            self.store.connection.execute(
                "UPDATE accounts SET notification = ? WHERE id = ?",
                (NOTIFICATIONS[origin], account_id),
            )

    def _set_balance(self, account_id: str, value: Decimal) -> None:
        self.store.connection.execute(
            "UPDATE accounts SET balance = ? WHERE id = ?",
            (money(value), account_id),
        )

    def _finish(self, reference: str, state: str) -> None:
        self.store.connection.execute(
            "UPDATE requests SET state = ? WHERE reference = ?", (state, reference)
        )
        self.store.event(reference, "request_finalized", state=state)


def run_scenario(world_path: Path, scenario_path: Path, db_path: Path = DEFAULT_DB) -> Store:
    world = load_json(world_path)
    scenario = load_json(scenario_path)
    store = Store(db_path)
    try:
        store.reset()
        engine = Engine(store)
        engine.load_world(world)
        engine.replay(scenario)
        return store
    except Exception:
        store.close()
        raise

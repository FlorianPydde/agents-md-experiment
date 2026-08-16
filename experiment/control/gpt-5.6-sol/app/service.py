from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .db import Database
from .errors import AppError


KINDS = {
    "goodwill_credit": ["read_account", "apply_credit", "notify_customer"],
    "account_recovery": [
        "read_account",
        "unfreeze_account",
        "apply_credit",
        "notify_customer",
    ],
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
POLICY_RULES = [
    {"rule": 1, "match": "materiality is read", "outcome": "automatic"},
    {"rule": 2, "match": "apply_credit amount <= 100.00", "outcome": "automatic"},
    {"rule": 3, "match": "apply_credit amount > 100.00", "outcome": "finance"},
    {"rule": 4, "match": "apply_debit", "outcome": "finance"},
    {"rule": 5, "match": "freeze_account or unfreeze_account", "outcome": "risk"},
    {"rule": 6, "match": "anything else", "outcome": "automatic"},
]
ORIGINS = {"internal", "external"}
DECISIONS = {"approve", "reject"}
APPROVER_ROLES = {"finance", "risk", "supervisor"}
FINAL_STATES = {"completed", "rejected", "failed"}
MESSAGES = {
    "internal": "Your internal service request has been completed.",
    "external": "Your service request has been completed.",
}


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise AppError(f"file not found: {path}") from None
    except (json.JSONDecodeError, OSError) as exc:
        raise AppError(f"cannot read {path}: {exc}") from None


def require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError(f"{context} must be an object")
    return value


def required(data: dict[str, Any], name: str, context: str) -> Any:
    if name not in data:
        raise AppError(f"{context} is missing required field '{name}'")
    return data[name]


def text(data: dict[str, Any], name: str, context: str) -> str:
    value = required(data, name, context)
    if not isinstance(value, str) or not value:
        raise AppError(f"{context}.{name} must be a non-empty string")
    return value


def amount(value: Any, context: str) -> Decimal:
    if not isinstance(value, str):
        raise AppError(f"{context} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise AppError(f"{context} must be a valid decimal amount") from None
    if not parsed.is_finite() or parsed < 0:
        raise AppError(f"{context} must be a non-negative decimal amount")
    return parsed


def money(value: Decimal) -> str:
    return f"{value:.2f}"


class Service:
    def __init__(self, database: Database):
        self.db = database

    def load_world(self, source: Any) -> None:
        world = require_object(source, "world")
        accounts = required(world, "accounts", "world")
        if not isinstance(accounts, list):
            raise AppError("world.accounts must be a list")
        seen: set[str] = set()
        for index, raw in enumerate(accounts):
            context = f"world.accounts[{index}]"
            account = require_object(raw, context)
            account_id = text(account, "id", context)
            if account_id in seen:
                raise AppError(f"duplicate account id: {account_id}")
            seen.add(account_id)
            owner = text(account, "owner", context)
            tier = text(account, "tier", context)
            if tier not in {"standard", "premium"}:
                raise AppError(f"{context}.tier must be standard or premium")
            balance = amount(required(account, "balance", context), f"{context}.balance")
            frozen = required(account, "frozen", context)
            if not isinstance(frozen, bool):
                raise AppError(f"{context}.frozen must be a boolean")
            self.db.connection.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                (account_id, owner, tier, money(balance), frozen),
            )
        self.db.connection.commit()

    def replay(self, source: Any) -> None:
        scenario = require_object(source, "scenario")
        steps = required(scenario, "steps", "scenario")
        if not isinstance(steps, list):
            raise AppError("scenario.steps must be a list")
        for index, raw in enumerate(steps):
            context = f"scenario.steps[{index}]"
            entry = require_object(raw, context)
            action = text(entry, "action", context)
            if action == "intake":
                self.intake(required(entry, "request", context))
            elif action == "decide":
                self.decide(
                    text(entry, "reference", context),
                    text(entry, "role", context),
                    text(entry, "decision", context),
                )
            else:
                raise AppError(f"{context}.action must be intake or decide")

    def intake(self, raw: Any) -> None:
        request = require_object(raw, "request")
        reference = text(request, "reference", "request")
        kind = text(request, "kind", "request")
        if kind not in KINDS:
            raise AppError(f"request.kind must be one of: {', '.join(KINDS)}")
        account_id = text(request, "account", "request")
        if self.db.connection.execute(
            "SELECT 1 FROM accounts WHERE id = ?", (account_id,)
        ).fetchone() is None:
            raise AppError(f"account not found: {account_id}")
        parsed_amount = amount(required(request, "amount", "request"), "request.amount")
        requester = require_object(required(request, "requester", "request"), "request.requester")
        requester_name = text(requester, "name", "request.requester")
        requester_role = text(requester, "role", "request.requester")
        origin = text(requester, "origin", "request.requester")
        if origin not in ORIGINS:
            raise AppError("request.requester.origin must be internal or external")
        if self.db.connection.execute(
            "SELECT 1 FROM requests WHERE reference = ?", (reference,)
        ).fetchone():
            raise AppError(f"duplicate request reference: {reference}")
        arrival = self.db.connection.execute(
            "SELECT COALESCE(MAX(arrival_order), 0) + 1 FROM requests"
        ).fetchone()[0]
        self.db.connection.execute(
            """INSERT INTO requests
               (reference, kind, account_id, amount, requester_name, requester_role,
                requester_origin, state, next_step, arrival_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'received', 0, ?)""",
            (
                reference,
                kind,
                account_id,
                money(parsed_amount),
                requester_name,
                requester_role,
                origin,
                arrival,
            ),
        )
        self.db.log(reference, "request_received", {"kind": kind, "account": account_id})
        for index, operation in enumerate(KINDS[kind]):
            arguments = {"account": account_id}
            if operation in {"apply_credit", "apply_debit"}:
                arguments["amount"] = money(parsed_amount)
            if operation == "notify_customer":
                arguments["origin"] = origin
            self.db.connection.execute(
                """INSERT INTO request_steps
                   (reference, step_index, operation, arguments, state)
                   VALUES (?, ?, ?, ?, 'queued')""",
                (reference, index, operation, json.dumps(arguments, separators=(",", ":"))),
            )
        self.db.log(reference, "plan_created", {"operations": KINDS[kind]})
        self.db.connection.commit()
        self._continue(reference)

    def policy(self, operation: str, arguments: dict[str, Any]) -> tuple[int, str | None]:
        if OPERATIONS[operation] == "read":
            return 1, None
        if operation == "apply_credit":
            if amount(arguments.get("amount"), "operation amount") <= Decimal("100.00"):
                return 2, None
            return 3, "finance"
        if operation == "apply_debit":
            return 4, "finance"
        if operation in {"freeze_account", "unfreeze_account"}:
            return 5, "risk"
        return 6, None

    def decide(self, reference: str, role: str, decision: str) -> None:
        if role not in APPROVER_ROLES:
            raise AppError(f"role must be one of: {', '.join(sorted(APPROVER_ROLES))}")
        if decision not in DECISIONS:
            raise AppError("decision must be approve or reject")
        approval = self.db.connection.execute(
            """SELECT id, step_index, required_role FROM approvals
               WHERE reference = ? AND status = 'pending'""",
            (reference,),
        ).fetchone()
        if approval is None:
            raise AppError(f"request {reference} has no pending approval")
        if role != approval["required_role"]:
            raise AppError(
                f"request {reference} requires approval from {approval['required_role']}, not {role}"
            )
        self.db.connection.execute(
            """UPDATE approvals SET status = 'resolved', decided_by = ?, decision = ?
               WHERE id = ?""",
            (role, decision, approval["id"]),
        )
        self.db.log(
            reference,
            "approval_resolved",
            {"step": approval["step_index"], "role": role, "decision": decision},
        )
        if decision == "reject":
            self.db.connection.execute(
                """UPDATE request_steps SET state = 'skipped'
                   WHERE reference = ? AND step_index >= ? AND state IN ('queued', 'awaiting_approval')""",
                (reference, approval["step_index"]),
            )
            self._finalize(reference, "rejected")
            self.db.connection.commit()
            return
        self.db.connection.execute(
            """UPDATE request_steps SET state = 'queued'
               WHERE reference = ? AND step_index = ?""",
            (reference, approval["step_index"]),
        )
        self.db.connection.execute(
            "UPDATE requests SET state = 'received' WHERE reference = ?", (reference,)
        )
        self.db.connection.commit()
        self._continue(reference, approved_step=approval["step_index"])

    def _continue(self, reference: str, approved_step: int | None = None) -> None:
        while True:
            request = self.db.connection.execute(
                """SELECT account_id, next_step, requester_origin
                   FROM requests WHERE reference = ?""",
                (reference,),
            ).fetchone()
            step = self.db.connection.execute(
                """SELECT step_index, operation, arguments FROM request_steps
                   WHERE reference = ? AND step_index = ?""",
                (reference, request["next_step"]),
            ).fetchone()
            if step is None:
                self._finalize(reference, "completed")
                self.db.connection.commit()
                return
            arguments = json.loads(step["arguments"])
            rule, required_role = self.policy(step["operation"], arguments)
            self.db.log(
                reference,
                "policy_decided",
                {
                    "step": step["step_index"],
                    "operation": step["operation"],
                    "rule": rule,
                    "outcome": required_role or "automatic",
                },
            )
            if required_role and approved_step != step["step_index"]:
                self.db.connection.execute(
                    """UPDATE request_steps SET state = 'awaiting_approval'
                       WHERE reference = ? AND step_index = ?""",
                    (reference, step["step_index"]),
                )
                self.db.connection.execute(
                    "UPDATE requests SET state = 'awaiting_approval' WHERE reference = ?",
                    (reference,),
                )
                self.db.connection.execute(
                    """INSERT INTO approvals(reference, step_index, required_role, status)
                       VALUES (?, ?, ?, 'pending')""",
                    (reference, step["step_index"], required_role),
                )
                self.db.log(
                    reference,
                    "approval_requested",
                    {"step": step["step_index"], "required_role": required_role},
                )
                self.db.connection.commit()
                return
            approved_step = None
            error = self._execute(reference, step["step_index"], step["operation"], arguments)
            if error:
                self.db.connection.execute(
                    """UPDATE request_steps SET state = 'skipped'
                       WHERE reference = ? AND step_index > ? AND state = 'queued'""",
                    (reference, step["step_index"]),
                )
                self._finalize(reference, "failed")
                self.db.connection.commit()
                return
            self.db.connection.execute(
                "UPDATE requests SET next_step = next_step + 1 WHERE reference = ?",
                (reference,),
            )
            self.db.connection.commit()

    def _execute(
        self, reference: str, step_index: int, operation: str, arguments: dict[str, Any]
    ) -> str | None:
        account = self.db.connection.execute(
            "SELECT balance, tier, frozen FROM accounts WHERE id = ?",
            (arguments["account"],),
        ).fetchone()
        if account is None:
            error = f"account not found: {arguments['account']}"
            self._operation_failed(reference, step_index, operation, error)
            return error
        self.db.log(
            reference,
            "operation_running",
            {"step": step_index, "operation": operation, "arguments": arguments},
        )
        if operation != "read_account" and operation != "unfreeze_account" and account["frozen"]:
            error = f"account {arguments['account']} is frozen"
            self._operation_failed(reference, step_index, operation, error)
            return error
        balance = Decimal(account["balance"])
        if operation == "apply_credit":
            balance += amount(arguments["amount"], "operation amount")
            self.db.connection.execute(
                "UPDATE accounts SET balance = ? WHERE id = ?",
                (money(balance), arguments["account"]),
            )
            result: dict[str, Any] = {"balance": money(balance)}
        elif operation == "apply_debit":
            debit = amount(arguments["amount"], "operation amount")
            if balance < debit:
                error = f"insufficient balance on account {arguments['account']}"
                self._operation_failed(reference, step_index, operation, error)
                return error
            balance -= debit
            self.db.connection.execute(
                "UPDATE accounts SET balance = ? WHERE id = ?",
                (money(balance), arguments["account"]),
            )
            result = {"balance": money(balance)}
        elif operation == "freeze_account":
            self.db.connection.execute(
                "UPDATE accounts SET frozen = 1 WHERE id = ?", (arguments["account"],)
            )
            result = {"frozen": True}
        elif operation == "unfreeze_account":
            self.db.connection.execute(
                "UPDATE accounts SET frozen = 0 WHERE id = ?", (arguments["account"],)
            )
            result = {"frozen": False}
        elif operation == "notify_customer":
            result = {"message": MESSAGES[arguments["origin"]]}
        else:
            result = {
                "balance": money(balance),
                "tier": account["tier"],
                "frozen": bool(account["frozen"]),
            }
        self.db.connection.execute(
            """UPDATE request_steps SET state = 'completed'
               WHERE reference = ? AND step_index = ?""",
            (reference, step_index),
        )
        self.db.log(
            reference,
            "operation_completed",
            {"step": step_index, "operation": operation, "result": result},
        )
        return None

    def _operation_failed(
        self, reference: str, step_index: int, operation: str, error: str
    ) -> None:
        self.db.connection.execute(
            """UPDATE request_steps SET state = 'failed'
               WHERE reference = ? AND step_index = ?""",
            (reference, step_index),
        )
        self.db.log(
            reference,
            "operation_failed",
            {"step": step_index, "operation": operation, "error": error},
        )

    def _finalize(self, reference: str, state: str) -> None:
        self.db.connection.execute(
            "UPDATE requests SET state = ? WHERE reference = ?", (state, reference)
        )
        self.db.log(reference, "request_finalized", {"state": state})

    def requests(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.connection.execute(
                """SELECT reference, kind, state, account_id AS account, amount
                   FROM requests ORDER BY arrival_order"""
            )
        ]

    def pending_approvals(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.connection.execute(
                """SELECT reference, step_index, required_role
                   FROM approvals WHERE status = 'pending' ORDER BY id"""
            )
        ]


from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .catalog import (
    KINDS,
    NOTIFICATION_TEMPLATES,
    OPERATIONS,
    POLICY_RULES,
    policy_for,
)
from .errors import AppError
from .storage import Store


VALID_ROLES = {"finance", "risk", "supervisor"}
VALID_DECISIONS = {"approve", "reject"}
VALID_ORIGINS = set(NOTIFICATION_TEMPLATES)
VALID_TIERS = {"standard", "premium"}
VALID_STATES = {"received", "awaiting_approval", "completed", "rejected", "failed"}


def load_json(path: Path, label: str) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as error:
        raise AppError(f"{label} file not found: {path}") from error
    except (json.JSONDecodeError, OSError) as error:
        raise AppError(f"malformed {label} file {path}: {error}") from error


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AppError(f"{label} must be a list")
    return value


def require_field(value: dict[str, Any], field: str, label: str) -> Any:
    if field not in value:
        raise AppError(f"{label} is missing required field '{field}'")
    return value[field]


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AppError(f"{label} must be a non-empty string")
    return value


def decimal_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise AppError(f"{label} must be a decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise AppError(f"{label} must be a valid decimal amount") from error
    if not amount.is_finite():
        raise AppError(f"{label} must be a finite decimal amount")
    if amount < 0:
        raise AppError(f"{label} must not be negative")
    return format(amount, "f")


def validate_world(raw: Any) -> list[dict[str, Any]]:
    world = require_mapping(raw, "world")
    accounts = require_list(require_field(world, "accounts", "world"), "world.accounts")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(accounts):
        label = f"world.accounts[{index}]"
        account = require_mapping(item, label)
        account_id = require_string(require_field(account, "id", label), f"{label}.id")
        if account_id in seen:
            raise AppError(f"duplicate account id: {account_id}")
        seen.add(account_id)
        tier = require_string(require_field(account, "tier", label), f"{label}.tier")
        if tier not in VALID_TIERS:
            raise AppError(f"{label}.tier must be one of: premium, standard")
        frozen = require_field(account, "frozen", label)
        if not isinstance(frozen, bool):
            raise AppError(f"{label}.frozen must be true or false")
        validated.append(
            {
                "id": account_id,
                "owner": require_string(
                    require_field(account, "owner", label), f"{label}.owner"
                ),
                "tier": tier,
                "balance": decimal_string(
                    require_field(account, "balance", label), f"{label}.balance"
                ),
                "frozen": frozen,
            }
        )
    return validated


def validate_request(raw: Any, label: str) -> dict[str, Any]:
    request = require_mapping(raw, label)
    kind = require_string(require_field(request, "kind", label), f"{label}.kind")
    if kind not in KINDS:
        raise AppError(f"{label}.kind must be one of: {', '.join(sorted(KINDS))}")
    requester_label = f"{label}.requester"
    requester = require_mapping(
        require_field(request, "requester", label), requester_label
    )
    origin = require_string(
        require_field(requester, "origin", requester_label),
        f"{requester_label}.origin",
    )
    if origin not in VALID_ORIGINS:
        raise AppError(f"{requester_label}.origin must be one of: external, internal")
    return {
        "reference": require_string(
            require_field(request, "reference", label), f"{label}.reference"
        ),
        "kind": kind,
        "account": require_string(
            require_field(request, "account", label), f"{label}.account"
        ),
        "amount": decimal_string(
            require_field(request, "amount", label), f"{label}.amount"
        ),
        "requester": {
            "name": require_string(
                require_field(requester, "name", requester_label),
                f"{requester_label}.name",
            ),
            "role": require_string(
                require_field(requester, "role", requester_label),
                f"{requester_label}.role",
            ),
            "origin": origin,
        },
    }


def validate_scenario(raw: Any) -> list[dict[str, Any]]:
    scenario = require_mapping(raw, "scenario")
    steps = require_list(
        require_field(scenario, "steps", "scenario"), "scenario.steps"
    )
    validated = []
    for index, item in enumerate(steps):
        label = f"scenario.steps[{index}]"
        entry = require_mapping(item, label)
        action = require_string(require_field(entry, "action", label), f"{label}.action")
        if action == "intake":
            validated.append(
                {
                    "action": action,
                    "request": validate_request(
                        require_field(entry, "request", label), f"{label}.request"
                    ),
                }
            )
        elif action == "decide":
            role = require_string(require_field(entry, "role", label), f"{label}.role")
            decision = require_string(
                require_field(entry, "decision", label), f"{label}.decision"
            )
            if role not in VALID_ROLES:
                raise AppError(
                    f"{label}.role must be one of: {', '.join(sorted(VALID_ROLES))}"
                )
            if decision not in VALID_DECISIONS:
                raise AppError(f"{label}.decision must be one of: approve, reject")
            validated.append(
                {
                    "action": action,
                    "reference": require_string(
                        require_field(entry, "reference", label), f"{label}.reference"
                    ),
                    "role": role,
                    "decision": decision,
                }
            )
        else:
            raise AppError(f"{label}.action must be one of: decide, intake")
    return validated


class Runner:
    def __init__(self, store: Store):
        self.store = store

    def seed_accounts(self, accounts: list[dict[str, Any]]) -> None:
        self.store.connection.executemany(
            """
            INSERT INTO accounts(id, owner, tier, balance, frozen)
            VALUES (:id, :owner, :tier, :balance, :frozen)
            """,
            accounts,
        )
        self.store.connection.commit()

    def replay(self, scenario: list[dict[str, Any]]) -> None:
        for entry in scenario:
            if entry["action"] == "intake":
                self.intake(entry["request"])
            else:
                self.decide(entry["reference"], entry["role"], entry["decision"])

    def intake(self, request: dict[str, Any]) -> None:
        account = self.store.account(request["account"])
        if account is None:
            raise AppError(
                f"request {request['reference']} names unknown account "
                f"{request['account']}"
            )
        if self.store.request_by_reference(request["reference"]) is not None:
            raise AppError(f"duplicate request reference: {request['reference']}")
        requester = request["requester"]
        cursor = self.store.connection.execute(
            """
            INSERT INTO requests(
                reference, kind, account_id, amount, requester_name,
                requester_role, requester_origin, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'received')
            """,
            (
                request["reference"],
                request["kind"],
                request["account"],
                request["amount"],
                requester["name"],
                requester["role"],
                requester["origin"],
            ),
        )
        request_id = cursor.lastrowid
        assert request_id is not None
        self.store.add_event(
            request_id,
            "request_received",
            {"reference": request["reference"], "kind": request["kind"]},
        )
        operations = KINDS[request["kind"]]
        for position, operation in enumerate(operations):
            arguments: dict[str, Any] = {"account": request["account"]}
            if operation in {"apply_credit", "apply_debit"}:
                arguments["amount"] = request["amount"]
            if operation == "notify_customer":
                arguments["origin"] = requester["origin"]
            self.store.connection.execute(
                """
                INSERT INTO steps(request_id, position, operation, arguments, state)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (
                    request_id,
                    position,
                    operation,
                    json.dumps(arguments, separators=(",", ":")),
                ),
            )
        self.store.add_event(
            request_id, "plan_created", {"operations": list(operations)}
        )
        self.store.connection.commit()
        self._continue(request_id)

    def decide(self, reference: str, role: str, decision: str) -> None:
        if role not in VALID_ROLES:
            raise AppError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        if decision not in VALID_DECISIONS:
            raise AppError("decision must be one of: approve, reject")
        request = self.store.request_by_reference(reference)
        if request is None:
            raise AppError(f"request not found: {reference}")
        approval = self.store.pending_approval(request["id"])
        if approval is None:
            raise AppError(f"request {reference} has no pending approval")
        if approval["required_role"] != role:
            raise AppError(
                f"request {reference} requires role {approval['required_role']}, "
                f"not {role}"
            )
        self.store.connection.execute(
            """
            UPDATE approvals
            SET status = 'resolved', decided_by_role = ?, decision = ?
            WHERE id = ?
            """,
            (role, decision, approval["id"]),
        )
        self.store.add_event(
            request["id"],
            "approval_resolved",
            {
                "step": approval["position"],
                "role": role,
                "decision": decision,
            },
        )
        if decision == "reject":
            self._finalize(request["id"], "rejected")
            self.store.connection.commit()
            return
        self.store.connection.execute(
            "UPDATE requests SET state = 'received' WHERE id = ?", (request["id"],)
        )
        self.store.connection.commit()
        if not self._execute_step(request["id"], approval["step_id"]):
            return
        self.store.connection.execute(
            "UPDATE requests SET current_step = ? WHERE id = ?",
            (approval["position"] + 1, request["id"]),
        )
        self.store.connection.commit()
        self._continue(request["id"])

    def _continue(self, request_id: int) -> None:
        while True:
            request = self.store.connection.execute(
                "SELECT * FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
            assert request is not None
            step = self.store.connection.execute(
                """
                SELECT * FROM steps
                WHERE request_id = ? AND position = ?
                """,
                (request_id, request["current_step"]),
            ).fetchone()
            if step is None:
                self._finalize(request_id, "completed")
                self.store.connection.commit()
                return
            arguments = json.loads(step["arguments"])
            rule, required_role = policy_for(step["operation"], arguments)
            self.store.add_event(
                request_id,
                "policy_decided",
                {
                    "step": step["position"],
                    "operation": step["operation"],
                    "rule": rule,
                    "required_role": required_role,
                },
            )
            if required_role is not None:
                self.store.connection.execute(
                    "UPDATE steps SET state = 'awaiting_approval' WHERE id = ?",
                    (step["id"],),
                )
                self.store.connection.execute(
                    """
                    INSERT INTO approvals(
                        request_id, step_id, required_role, status
                    ) VALUES (?, ?, ?, 'pending')
                    """,
                    (request_id, step["id"], required_role),
                )
                self.store.connection.execute(
                    "UPDATE requests SET state = 'awaiting_approval' WHERE id = ?",
                    (request_id,),
                )
                self.store.add_event(
                    request_id,
                    "approval_requested",
                    {"step": step["position"], "role": required_role},
                )
                self.store.connection.commit()
                return
            if not self._execute_step(request_id, step["id"]):
                return
            self.store.connection.execute(
                "UPDATE requests SET current_step = current_step + 1 WHERE id = ?",
                (request_id,),
            )
            self.store.connection.commit()

    def _execute_step(self, request_id: int, step_id: int) -> bool:
        step = self.store.connection.execute(
            "SELECT * FROM steps WHERE id = ?", (step_id,)
        ).fetchone()
        assert step is not None
        arguments = json.loads(step["arguments"])
        operation = step["operation"]
        try:
            result = self._operate(request_id, operation, arguments)
        except AppError as error:
            self.store.connection.execute(
                "UPDATE steps SET state = 'failed' WHERE id = ?", (step_id,)
            )
            self.store.add_event(
                request_id,
                "operation_failed",
                {
                    "step": step["position"],
                    "operation": operation,
                    "error": str(error),
                },
            )
            self._finalize(request_id, "failed")
            self.store.connection.commit()
            return False
        self.store.connection.execute(
            "UPDATE steps SET state = 'completed' WHERE id = ?", (step_id,)
        )
        self.store.add_event(
            request_id,
            "operation_ran",
            {
                "step": step["position"],
                "operation": operation,
                "result": result,
            },
        )
        self.store.connection.commit()
        return True

    def _operate(
        self, request_id: int, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if operation not in OPERATIONS:
            raise AppError(f"unsupported operation: {operation}")
        account_id = arguments.get("account")
        if not isinstance(account_id, str) or not account_id:
            raise AppError(f"{operation} requires an account")
        account = self.store.account(account_id)
        if account is None:
            raise AppError(f"account not found: {account_id}")
        if (
            OPERATIONS[operation]["materiality"] == "write"
            and operation != "unfreeze_account"
            and account["frozen"]
        ):
            raise AppError(f"account {account_id} is frozen")
        if operation == "read_account":
            return {
                "balance": account["balance"],
                "tier": account["tier"],
                "frozen": bool(account["frozen"]),
            }
        if operation in {"apply_credit", "apply_debit"}:
            amount = Decimal(decimal_string(arguments.get("amount"), "operation amount"))
            balance = Decimal(account["balance"])
            if operation == "apply_debit" and balance < amount:
                raise AppError(
                    f"account {account_id} balance is below debit amount "
                    f"{format(amount, 'f')}"
                )
            new_balance = balance + amount if operation == "apply_credit" else balance - amount
            formatted = format(new_balance, "f")
            self.store.connection.execute(
                "UPDATE accounts SET balance = ? WHERE id = ?",
                (formatted, account_id),
            )
            return {"balance": formatted}
        if operation in {"freeze_account", "unfreeze_account"}:
            frozen = operation == "freeze_account"
            self.store.connection.execute(
                "UPDATE accounts SET frozen = ? WHERE id = ?",
                (frozen, account_id),
            )
            return {"frozen": frozen}
        origin = arguments.get("origin")
        if origin not in NOTIFICATION_TEMPLATES:
            raise AppError("notify_customer requires origin internal or external")
        message = NOTIFICATION_TEMPLATES[origin]
        self.store.connection.execute(
            """
            INSERT INTO notifications(request_id, account_id, message)
            VALUES (?, ?, ?)
            """,
            (request_id, account_id, message),
        )
        return {"message": message}

    def _finalize(self, request_id: int, state: str) -> None:
        if state not in VALID_STATES:
            raise AppError(f"invalid final state: {state}")
        if state in {"rejected", "failed"}:
            self.store.connection.execute(
                """
                UPDATE steps SET state = 'skipped'
                WHERE request_id = ? AND state IN ('pending', 'awaiting_approval')
                """,
                (request_id,),
            )
        self.store.connection.execute(
            "UPDATE requests SET state = ? WHERE id = ?", (state, request_id)
        )
        self.store.add_event(request_id, "request_finalized", {"state": state})

    @staticmethod
    def operations() -> list[dict[str, str]]:
        return [
            {"name": name, **description}
            for name, description in OPERATIONS.items()
        ]

    @staticmethod
    def policy_rules() -> list[dict[str, Any]]:
        return POLICY_RULES

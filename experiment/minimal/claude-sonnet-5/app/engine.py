"""The engine: intake, policy application, running steps, and approvals.

This module contains all of the stateful behaviour described under "Request
lifecycle", "Plans", "Policy" and "Running a request" in SPEC.md. It reads
and writes the SQLite database in `db.py` and never talks to the terminal or
network directly (that is the job of the commands and the HTTP API).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from app import db
from app.domain import MATERIALITY, PLANS, decide_policy
from app.errors import AppError
from app.operations import Account, OperationError, run_operation
from app.scenario import DecideAction, IntakeAction
from app.world import load_world

TERMINAL_STATES = ("completed", "rejected", "failed")


def build_step_args(kind: str, account: str, amount: Decimal, origin: str) -> list[dict]:
    """Build the ordered argument dicts for the plan of a request kind."""

    args_by_operation = {
        "read_account": {"account": account},
        "apply_credit": {"account": account, "amount": str(amount)},
        "apply_debit": {"account": account, "amount": str(amount)},
        "unfreeze_account": {"account": account},
        "freeze_account": {"account": account},
        "notify_customer": {"account": account, "origin": origin},
    }
    return [dict(args_by_operation[op]) for op in PLANS[kind]]


class Engine:
    def __init__(self, conn: sqlite3.Connection, accounts: dict[str, Account]):
        self.conn = conn
        self.accounts = accounts

    # -- construction ---------------------------------------------------

    @classmethod
    def fresh(cls, world_path: str | Path, db_path: str | Path) -> "Engine":
        """Start from a clean database and the accounts in world.json."""

        accounts = load_world(world_path)
        conn = db.fresh_database(db_path)
        for account in accounts.values():
            conn.execute(
                "INSERT INTO accounts (id, owner, tier, balance, frozen) "
                "VALUES (?, ?, ?, ?, ?)",
                (account.id, account.owner, account.tier, str(account.balance), int(account.frozen)),
            )
        conn.commit()
        return cls(conn, accounts)

    @classmethod
    def open_existing(cls, db_path: str | Path) -> "Engine":
        """Open a database written by a previous `run`."""

        p = Path(db_path)
        if not p.exists():
            raise AppError(f"{db_path}: no database found; run the `run` command first")
        conn = db.connect(p)
        db.init_schema(conn)
        accounts: dict[str, Account] = {}
        for row in conn.execute("SELECT * FROM accounts"):
            accounts[row["id"]] = Account(
                id=row["id"],
                owner=row["owner"],
                tier=row["tier"],
                balance=Decimal(row["balance"]),
                frozen=bool(row["frozen"]),
            )
        return cls(conn, accounts)

    # -- scenario replay --------------------------------------------------

    def run_scenario(self, actions: list) -> None:
        for action in actions:
            if isinstance(action, IntakeAction):
                self.intake(action)
            elif isinstance(action, DecideAction):
                self.decide(action)
            else:  # pragma: no cover - defensive
                raise AppError(f"unknown scenario action {action!r}")

    # -- intake -----------------------------------------------------------

    def intake(self, action: IntakeAction) -> None:
        if action.account not in self.accounts:
            raise AppError(
                f"request {action.reference} refers to unknown account '{action.account}'"
            )
        existing = self.conn.execute(
            "SELECT 1 FROM requests WHERE reference = ?", (action.reference,)
        ).fetchone()
        if existing is not None:
            raise AppError(f"duplicate request reference '{action.reference}'")

        seq = self.conn.execute("SELECT COUNT(*) AS n FROM requests").fetchone()["n"]

        self.conn.execute(
            "INSERT INTO requests (reference, kind, account, amount, requester_name, "
            "requester_role, requester_origin, state, next_step_index, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'received', 0, ?)",
            (
                action.reference,
                action.kind,
                action.account,
                str(action.amount),
                action.requester.name,
                action.requester.role,
                action.requester.origin,
                seq,
            ),
        )
        self.conn.commit()
        db.append_log(
            self.conn,
            "request_received",
            action.reference,
            {
                "kind": action.kind,
                "account": action.account,
                "amount": str(action.amount),
                "requester": asdict(action.requester),
            },
        )

        operations = PLANS[action.kind]
        step_args = build_step_args(action.kind, action.account, action.amount, action.requester.origin)
        for index, (operation, args) in enumerate(zip(operations, step_args)):
            self.conn.execute(
                "INSERT INTO steps (request_reference, step_index, operation, args, state) "
                "VALUES (?, ?, ?, ?, 'pending')",
                (action.reference, index, operation, json.dumps(args)),
            )
        self.conn.commit()
        db.append_log(
            self.conn,
            "plan_created",
            action.reference,
            {"steps": [{"index": i, "operation": op} for i, op in enumerate(operations)]},
        )

        self._advance(action.reference)

    # -- running steps ------------------------------------------------

    def _advance(self, reference: str) -> None:
        row = self._request_row(reference)
        if row["state"] in TERMINAL_STATES:
            return

        steps = self._steps(reference)
        next_index = row["next_step_index"]

        while next_index < len(steps):
            step = steps[next_index]
            operation = step["operation"]
            args = json.loads(step["args"])
            amount = Decimal(args["amount"]) if "amount" in args else None
            decision = decide_policy(operation, amount)
            db.append_log(
                self.conn,
                "policy_decision",
                reference,
                {
                    "step_index": next_index,
                    "operation": operation,
                    "auto": decision.auto,
                    "role": decision.role,
                },
            )

            if not decision.auto:
                self.conn.execute(
                    "INSERT INTO approvals (request_reference, step_index, role_required, status) "
                    "VALUES (?, ?, ?, 'pending')",
                    (reference, next_index, decision.role),
                )
                self._set_state(reference, "awaiting_approval")
                self.conn.commit()
                db.append_log(
                    self.conn,
                    "approval_requested",
                    reference,
                    {"step_index": next_index, "operation": operation, "role": decision.role},
                )
                return

            if not self._execute_step(reference, next_index, operation, args):
                return

            next_index += 1
            self._set_next_index(reference, next_index)

        self._set_state(reference, "completed")
        db.append_log(self.conn, "request_finalized", reference, {"state": "completed"})

    def _execute_step(self, reference: str, index: int, operation: str, args: dict) -> bool:
        account = self.accounts[args["account"]]
        try:
            result = run_operation(operation, account, args)
        except OperationError as exc:
            self._mark_step(reference, index, "failed")
            self._skip_remaining(reference, index + 1)
            db.append_log(
                self.conn,
                "operation_failed",
                reference,
                {"step_index": index, "operation": operation, "error": str(exc)},
            )
            self._set_state(reference, "failed")
            db.append_log(self.conn, "request_finalized", reference, {"state": "failed"})
            return False

        self._persist_account(account)
        self._mark_step(reference, index, "done")
        db.append_log(
            self.conn,
            "operation_run",
            reference,
            {"step_index": index, "operation": operation, "result": result},
        )
        return True

    # -- decisions ----------------------------------------------------

    def decide(self, action: DecideAction) -> None:
        approval = self.conn.execute(
            "SELECT * FROM approvals WHERE request_reference = ? AND status = 'pending' "
            "ORDER BY id LIMIT 1",
            (action.reference,),
        ).fetchone()
        if approval is None:
            raise AppError(f"no pending approval for request '{action.reference}'")
        if approval["role_required"] != action.role:
            raise AppError(
                f"decision for '{action.reference}' requires role "
                f"'{approval['role_required']}', not '{action.role}'"
            )

        db.append_log(
            self.conn,
            "approval_resolved",
            action.reference,
            {
                "step_index": approval["step_index"],
                "role": action.role,
                "decision": action.decision,
            },
        )
        self.conn.execute(
            "UPDATE approvals SET status = ?, decided_role = ?, decision = ? WHERE id = ?",
            (
                "approved" if action.decision == "approve" else "rejected",
                action.role,
                action.decision,
                approval["id"],
            ),
        )
        self.conn.commit()

        step_index = approval["step_index"]
        if action.decision == "reject":
            self._mark_step(action.reference, step_index, "skipped")
            self._skip_remaining(action.reference, step_index + 1)
            self._set_state(action.reference, "rejected")
            db.append_log(
                self.conn, "request_finalized", action.reference, {"state": "rejected"}
            )
            return

        step = self._steps(action.reference)[step_index]
        args = json.loads(step["args"])
        if not self._execute_step(action.reference, step_index, step["operation"], args):
            return
        self._set_next_index(action.reference, step_index + 1)
        self._advance(action.reference)

    # -- lookups --------------------------------------------------------

    def list_requests(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM requests ORDER BY seq").fetchall()

    def get_request(self, reference: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()

    def _steps(self, reference: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM steps WHERE request_reference = ? ORDER BY step_index", (reference,)
        ).fetchall()

    def get_steps(self, reference: str) -> list[sqlite3.Row]:
        return self._steps(reference)

    def get_approvals(self, reference: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE request_reference = ? ORDER BY id", (reference,)
        ).fetchall()

    def list_pending_approvals(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY id"
        ).fetchall()

    def get_log(self, reference: str) -> list[dict]:
        return db.log_entries_for(self.conn, reference)

    def list_accounts(self) -> list[Account]:
        return sorted(self.accounts.values(), key=lambda a: a.id)

    # -- small mutation helpers -------------------------------------------

    def _request_row(self, reference: str) -> sqlite3.Row:
        row = self.get_request(reference)
        if row is None:  # pragma: no cover - defensive
            raise AppError(f"unknown request '{reference}'")
        return row

    def _set_state(self, reference: str, state: str) -> None:
        self.conn.execute("UPDATE requests SET state = ? WHERE reference = ?", (state, reference))
        self.conn.commit()

    def _set_next_index(self, reference: str, index: int) -> None:
        self.conn.execute(
            "UPDATE requests SET next_step_index = ? WHERE reference = ?", (index, reference)
        )
        self.conn.commit()

    def _mark_step(self, reference: str, index: int, state: str) -> None:
        self.conn.execute(
            "UPDATE steps SET state = ? WHERE request_reference = ? AND step_index = ?",
            (state, reference, index),
        )
        self.conn.commit()

    def _skip_remaining(self, reference: str, from_index: int) -> None:
        self.conn.execute(
            "UPDATE steps SET state = 'skipped' WHERE request_reference = ? "
            "AND step_index >= ? AND state = 'pending'",
            (reference, from_index),
        )
        self.conn.commit()

    def _persist_account(self, account: Account) -> None:
        self.conn.execute(
            "UPDATE accounts SET balance = ?, frozen = ? WHERE id = ?",
            (str(account.balance), int(account.frozen), account.id),
        )
        self.conn.commit()

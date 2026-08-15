"""SQLite-backed persistent store for the log, requests, steps, approvals,
and account state.

The database survives between runs. `run` starts from a clean database (the
file is removed/recreated); `show`, `export`, and `serve` open the existing
database read-only or read-write as needed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.domain import (
    Account,
    ApprovalState,
    ApproverRole,
    OperationName,
    RequestKind,
    RequestState,
    StepState,
    Tier,
)
from app.errors import AppError
from app.log import LogEntry, LogEventKind

DEFAULT_DB_NAME = "runner.db"

SCHEMA = """
CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    tier TEXT NOT NULL,
    balance TEXT NOT NULL,
    frozen INTEGER NOT NULL
);

CREATE TABLE requests (
    reference TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    account TEXT NOT NULL,
    amount TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state TEXT NOT NULL,
    order_index INTEGER NOT NULL
);

CREATE TABLE steps (
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (reference, step_index)
);

CREATE TABLE approvals (
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    required_role TEXT NOT NULL,
    state TEXT NOT NULL,
    resolved_by_role TEXT,
    PRIMARY KEY (reference, step_index)
);

CREATE TABLE log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    reference TEXT,
    data TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class StoredRequest:
    reference: str
    kind: RequestKind
    account: str
    amount: Decimal
    state: RequestState
    order_index: int


@dataclass(frozen=True)
class StoredStep:
    reference: str
    step_index: int
    operation: OperationName
    state: StepState


@dataclass(frozen=True)
class StoredApproval:
    reference: str
    step_index: int
    required_role: ApproverRole
    state: ApprovalState
    resolved_by_role: ApproverRole | None


class Store:
    """Owns the SQLite connection and all reads/writes against it."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")

    @classmethod
    def create_fresh(cls, db_path: Path) -> "Store":
        """Delete any existing database and start with a clean schema."""
        if db_path.exists():
            db_path.unlink()
        store = cls(db_path)
        store._conn.executescript(SCHEMA)
        store._conn.commit()
        return store

    @classmethod
    def open_existing(cls, db_path: Path) -> "Store":
        if not db_path.exists():
            raise AppError(f"database not found: {db_path} (run 'run' first)")
        return cls(db_path)

    def close(self) -> None:
        self._conn.close()

    # -- accounts ---------------------------------------------------------

    def put_account(self, account: Account) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO accounts (id, owner, tier, balance, frozen) VALUES (?, ?, ?, ?, ?)",
            (account.id, account.owner, account.tier.value, str(account.balance), int(account.frozen)),
        )

    def get_account(self, account_id: str) -> Account | None:
        row = self._conn.execute(
            "SELECT id, owner, tier, balance, frozen FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if row is None:
            return None
        return Account(
            id=row[0], owner=row[1], tier=Tier(row[2]), balance=Decimal(row[3]), frozen=bool(row[4])
        )

    def all_accounts(self) -> list[Account]:
        rows = self._conn.execute(
            "SELECT id, owner, tier, balance, frozen FROM accounts ORDER BY id"
        ).fetchall()
        return [
            Account(id=r[0], owner=r[1], tier=Tier(r[2]), balance=Decimal(r[3]), frozen=bool(r[4]))
            for r in rows
        ]

    # -- requests -----------------------------------------------------------

    def insert_request(
        self,
        reference: str,
        kind: RequestKind,
        account: str,
        amount: Decimal,
        requester_name: str,
        requester_role: str,
        requester_origin: str,
        state: RequestState,
        order_index: int,
    ) -> None:
        self._conn.execute(
            """INSERT INTO requests
               (reference, kind, account, amount, requester_name, requester_role,
                requester_origin, state, order_index)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                reference,
                kind.value,
                account,
                str(amount),
                requester_name,
                requester_role,
                requester_origin,
                state.value,
                order_index,
            ),
        )

    def update_request_state(self, reference: str, state: RequestState) -> None:
        self._conn.execute(
            "UPDATE requests SET state = ? WHERE reference = ?", (state.value, reference)
        )

    def get_request(self, reference: str) -> StoredRequest | None:
        row = self._conn.execute(
            "SELECT reference, kind, account, amount, state, order_index FROM requests WHERE reference = ?",
            (reference,),
        ).fetchone()
        if row is None:
            return None
        return StoredRequest(
            reference=row[0],
            kind=RequestKind(row[1]),
            account=row[2],
            amount=Decimal(row[3]),
            state=RequestState(row[4]),
            order_index=row[5],
        )

    def all_requests_in_order(self) -> list[StoredRequest]:
        rows = self._conn.execute(
            "SELECT reference, kind, account, amount, state, order_index FROM requests ORDER BY order_index"
        ).fetchall()
        return [
            StoredRequest(
                reference=r[0],
                kind=RequestKind(r[1]),
                account=r[2],
                amount=Decimal(r[3]),
                state=RequestState(r[4]),
                order_index=r[5],
            )
            for r in rows
        ]

    def get_request_requester(self, reference: str) -> tuple[str, str, str] | None:
        row = self._conn.execute(
            "SELECT requester_name, requester_role, requester_origin FROM requests WHERE reference = ?",
            (reference,),
        ).fetchone()
        return tuple(row) if row else None

    # -- steps ----------------------------------------------------------

    def insert_step(
        self, reference: str, step_index: int, operation: OperationName, state: StepState
    ) -> None:
        self._conn.execute(
            "INSERT INTO steps (reference, step_index, operation, state) VALUES (?, ?, ?, ?)",
            (reference, step_index, operation.value, state.value),
        )

    def update_step_state(self, reference: str, step_index: int, state: StepState) -> None:
        self._conn.execute(
            "UPDATE steps SET state = ? WHERE reference = ? AND step_index = ?",
            (state.value, reference, step_index),
        )

    def get_steps(self, reference: str) -> list[StoredStep]:
        rows = self._conn.execute(
            "SELECT reference, step_index, operation, state FROM steps "
            "WHERE reference = ? ORDER BY step_index",
            (reference,),
        ).fetchall()
        return [
            StoredStep(
                reference=r[0], step_index=r[1], operation=OperationName(r[2]), state=StepState(r[3])
            )
            for r in rows
        ]

    # -- approvals --------------------------------------------------------

    def insert_approval(
        self, reference: str, step_index: int, required_role: ApproverRole, state: ApprovalState
    ) -> None:
        self._conn.execute(
            "INSERT INTO approvals (reference, step_index, required_role, state, resolved_by_role) "
            "VALUES (?, ?, ?, ?, NULL)",
            (reference, step_index, required_role.value, state.value),
        )

    def resolve_approval(
        self, reference: str, step_index: int, state: ApprovalState, resolved_by_role: ApproverRole
    ) -> None:
        self._conn.execute(
            "UPDATE approvals SET state = ?, resolved_by_role = ? WHERE reference = ? AND step_index = ?",
            (state.value, resolved_by_role.value, reference, step_index),
        )

    def get_pending_approval(self, reference: str) -> StoredApproval | None:
        row = self._conn.execute(
            "SELECT reference, step_index, required_role, state, resolved_by_role "
            "FROM approvals WHERE reference = ? AND state = ? ORDER BY step_index DESC LIMIT 1",
            (reference, ApprovalState.PENDING.value),
        ).fetchone()
        if row is None:
            return None
        return StoredApproval(
            reference=row[0],
            step_index=row[1],
            required_role=ApproverRole(row[2]),
            state=ApprovalState(row[3]),
            resolved_by_role=ApproverRole(row[4]) if row[4] else None,
        )

    def all_pending_approvals(self) -> list[StoredApproval]:
        rows = self._conn.execute(
            "SELECT reference, step_index, required_role, state, resolved_by_role "
            "FROM approvals WHERE state = ? ORDER BY reference, step_index",
            (ApprovalState.PENDING.value,),
        ).fetchall()
        return [
            StoredApproval(
                reference=r[0],
                step_index=r[1],
                required_role=ApproverRole(r[2]),
                state=ApprovalState(r[3]),
                resolved_by_role=ApproverRole(r[4]) if r[4] else None,
            )
            for r in rows
        ]

    def get_approvals(self, reference: str) -> list[StoredApproval]:
        rows = self._conn.execute(
            "SELECT reference, step_index, required_role, state, resolved_by_role "
            "FROM approvals WHERE reference = ? ORDER BY step_index",
            (reference,),
        ).fetchall()
        return [
            StoredApproval(
                reference=r[0],
                step_index=r[1],
                required_role=ApproverRole(r[2]),
                state=ApprovalState(r[3]),
                resolved_by_role=ApproverRole(r[4]) if r[4] else None,
            )
            for r in rows
        ]

    # -- log ----------------------------------------------------------------

    def append_log(self, kind: LogEventKind, reference: str | None, data: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO log (kind, reference, data) VALUES (?, ?, ?)",
            (kind.value, reference, json.dumps(data)),
        )

    def get_log(self, reference: str) -> list[LogEntry]:
        rows = self._conn.execute(
            "SELECT seq, kind, reference, data FROM log WHERE reference = ? ORDER BY seq",
            (reference,),
        ).fetchall()
        return [
            LogEntry(seq=r[0], kind=LogEventKind(r[1]), reference=r[2], data=json.loads(r[3]))
            for r in rows
        ]

    def get_all_log(self) -> list[LogEntry]:
        rows = self._conn.execute("SELECT seq, kind, reference, data FROM log ORDER BY seq").fetchall()
        return [
            LogEntry(seq=r[0], kind=LogEventKind(r[1]), reference=r[2], data=json.loads(r[3]))
            for r in rows
        ]

    def commit(self) -> None:
        self._conn.commit()

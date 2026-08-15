"""SQLite-backed persistent store for accounts, requests, steps, approvals and log.

The log is append-only: rows are inserted and never updated or deleted.
Everything else (accounts, requests, steps, approvals) is mutable state that
reflects the current point in the replay, but every meaningful transition is
also captured as a log entry so the full history can be reconstructed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    tier TEXT NOT NULL,
    balance TEXT NOT NULL,
    frozen INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    reference TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    account TEXT NOT NULL,
    amount TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state TEXT NOT NULL,
    next_step_index INTEGER NOT NULL,
    seq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS request_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    UNIQUE(reference, step_index)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS log_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    reference TEXT,
    data TEXT NOT NULL
);
"""


@dataclass
class AccountRow:
    id: str
    owner: str
    tier: str
    balance: Decimal
    frozen: bool

    @property
    def balance_str(self) -> str:
        return f"{self.balance:.2f}"


class Store:
    """Thin wrapper around the SQLite database backing the whole system."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._seq_counter = 0

    @classmethod
    def fresh(cls, db_path: Path) -> "Store":
        """Delete any existing database file and start clean."""
        if db_path.exists():
            db_path.unlink()
        store = cls(db_path)
        store._conn.executescript(SCHEMA)
        store._conn.commit()
        return store

    @classmethod
    def open_existing(cls, db_path: Path) -> "Store":
        """Open a database that must already exist."""
        store = cls(db_path)
        store._conn.executescript(SCHEMA)
        store._conn.commit()
        row = store._conn.execute("SELECT MAX(seq) AS m FROM log_entries").fetchone()
        store._seq_counter = (row["m"] or 0)
        return store

    def close(self) -> None:
        self._conn.close()

    def _next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter

    # -- accounts ---------------------------------------------------------

    def seed_accounts(self, accounts) -> None:
        for acc in accounts:
            self._conn.execute(
                "INSERT INTO accounts (id, owner, tier, balance, frozen) VALUES (?, ?, ?, ?, ?)",
                (acc.id, acc.owner, acc.tier, f"{acc.balance:.2f}", int(acc.frozen)),
            )
        self._conn.commit()

    def get_account(self, account_id: str) -> AccountRow | None:
        row = self._conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if row is None:
            return None
        return AccountRow(
            id=row["id"],
            owner=row["owner"],
            tier=row["tier"],
            balance=Decimal(row["balance"]),
            frozen=bool(row["frozen"]),
        )

    def save_account(self, account: AccountRow) -> None:
        self._conn.execute(
            "UPDATE accounts SET balance = ?, frozen = ? WHERE id = ?",
            (account.balance_str, int(account.frozen), account.id),
        )
        self._conn.commit()

    def list_accounts(self) -> list[AccountRow]:
        rows = self._conn.execute("SELECT * FROM accounts ORDER BY id ASC").fetchall()
        return [
            AccountRow(
                id=r["id"],
                owner=r["owner"],
                tier=r["tier"],
                balance=Decimal(r["balance"]),
                frozen=bool(r["frozen"]),
            )
            for r in rows
        ]

    # -- requests -----------------------------------------------------------

    def create_request(self, request, seq: int) -> None:
        self._conn.execute(
            """INSERT INTO requests
               (reference, kind, account, amount, requester_name, requester_role,
                requester_origin, state, next_step_index, seq)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request.reference,
                request.kind,
                request.account,
                f"{request.amount:.2f}",
                request.requester.name,
                request.requester.role,
                request.requester.origin,
                "received",
                0,
                seq,
            ),
        )
        self._conn.commit()

    def get_request(self, reference: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()

    def list_requests(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM requests ORDER BY seq ASC").fetchall()

    def update_request_state(self, reference: str, state: str) -> None:
        self._conn.execute(
            "UPDATE requests SET state = ? WHERE reference = ?", (state, reference)
        )
        self._conn.commit()

    def advance_request_step(self, reference: str, next_step_index: int) -> None:
        self._conn.execute(
            "UPDATE requests SET next_step_index = ? WHERE reference = ?",
            (next_step_index, reference),
        )
        self._conn.commit()

    # -- request steps -------------------------------------------------------

    def create_plan_steps(self, reference: str, operations: list[str]) -> None:
        for idx, op in enumerate(operations):
            self._conn.execute(
                """INSERT INTO request_steps (reference, step_index, operation, state)
                   VALUES (?, ?, ?, ?)""",
                (reference, idx, op, "pending"),
            )
        self._conn.commit()

    def list_steps(self, reference: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM request_steps WHERE reference = ? ORDER BY step_index ASC",
            (reference,),
        ).fetchall()

    def update_step_state(self, reference: str, step_index: int, state: str) -> None:
        self._conn.execute(
            "UPDATE request_steps SET state = ? WHERE reference = ? AND step_index = ?",
            (state, reference, step_index),
        )
        self._conn.commit()

    # -- approvals ------------------------------------------------------------

    def create_approval(self, reference: str, step_index: int, role: str) -> int:
        cur = self._conn.execute(
            """INSERT INTO approvals (reference, step_index, role, state)
               VALUES (?, ?, ?, ?)""",
            (reference, step_index, role, "pending"),
        )
        self._conn.commit()
        return cur.lastrowid

    def find_pending_approval(self, reference: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT * FROM approvals WHERE reference = ? AND state = 'pending'
               ORDER BY id DESC LIMIT 1""",
            (reference,),
        ).fetchone()

    def resolve_approval(self, approval_id: int, state: str) -> None:
        self._conn.execute(
            "UPDATE approvals SET state = ? WHERE id = ?", (state, approval_id)
        )
        self._conn.commit()

    def list_approvals(self, reference: str | None = None) -> list[sqlite3.Row]:
        if reference is None:
            return self._conn.execute(
                "SELECT * FROM approvals ORDER BY id ASC"
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM approvals WHERE reference = ? ORDER BY id ASC", (reference,)
        ).fetchall()

    def list_pending_approvals(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM approvals WHERE state = 'pending' ORDER BY id ASC"
        ).fetchall()

    # -- log ------------------------------------------------------------------

    def append_log(self, event_type: str, reference: str | None, data: dict) -> int:
        seq = self._next_seq()
        self._conn.execute(
            "INSERT INTO log_entries (seq, event_type, reference, data) VALUES (?, ?, ?, ?)",
            (seq, event_type, reference, json.dumps(data, sort_keys=True)),
        )
        self._conn.commit()
        return seq

    def list_log(self, reference: str | None = None) -> list[dict]:
        if reference is None:
            rows = self._conn.execute(
                "SELECT * FROM log_entries ORDER BY seq ASC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM log_entries WHERE reference = ? ORDER BY seq ASC",
                (reference,),
            ).fetchall()
        return [
            {
                "seq": r["seq"],
                "event_type": r["event_type"],
                "reference": r["reference"],
                "data": json.loads(r["data"]),
            }
            for r in rows
        ]

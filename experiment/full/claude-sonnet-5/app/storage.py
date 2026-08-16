"""Persistent storage: accounts, requests, steps, approvals and the log.

Everything lives in a single SQLite database file in this folder so that the
log survives between runs. The ``run`` command always starts from a clean
database; other commands open the existing one and fail clearly if it is
missing.
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional

from app.errors import AppError

DB_FILENAME = "log.db"

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
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    account TEXT NOT NULL,
    amount TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state TEXT NOT NULL,
    next_step_index INTEGER NOT NULL
);

CREATE TABLE steps (
    request_reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (request_reference, step_index)
);

CREATE TABLE approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    role_required TEXT NOT NULL,
    status TEXT NOT NULL,
    decided_by_role TEXT
);

CREATE TABLE log_entries (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    request_reference TEXT,
    data TEXT NOT NULL
);
"""


def default_db_path(base_dir: Path) -> Path:
    return base_dir / DB_FILENAME


class Storage:
    """Thin wrapper around the SQLite database backing the system."""

    def __init__(self, connection: sqlite3.Connection):
        self.conn = connection
        self.conn.row_factory = sqlite3.Row

    @staticmethod
    def create_fresh(path: Path) -> "Storage":
        """Delete any existing database and create a clean one."""

        if path.exists():
            path.unlink()
        conn = sqlite3.connect(str(path))
        conn.executescript(SCHEMA)
        conn.commit()
        return Storage(conn)

    @staticmethod
    def open_existing(path: Path) -> "Storage":
        if not path.exists():
            raise AppError(
                f"no database found at {path}; run the 'run' command first"
            )
        conn = sqlite3.connect(str(path))
        return Storage(conn)

    def close(self) -> None:
        self.conn.close()

    # -- accounts ---------------------------------------------------

    def insert_account(self, account_id: str, owner: str, tier: str, balance: Decimal, frozen: bool) -> None:
        self.conn.execute(
            "INSERT INTO accounts (id, owner, tier, balance, frozen) VALUES (?, ?, ?, ?, ?)",
            (account_id, owner, tier, str(balance), int(frozen)),
        )

    def get_account(self, account_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()

    def update_account(self, account_id: str, balance: Decimal, frozen: bool) -> None:
        self.conn.execute(
            "UPDATE accounts SET balance = ?, frozen = ? WHERE id = ?",
            (str(balance), int(frozen), account_id),
        )

    def all_accounts(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()

    # -- requests ---------------------------------------------------

    def insert_request(
        self,
        reference: str,
        seq: int,
        kind: str,
        account: str,
        amount: Decimal,
        requester_name: str,
        requester_role: str,
        requester_origin: str,
        state: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO requests (
                reference, seq, kind, account, amount,
                requester_name, requester_role, requester_origin,
                state, next_step_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                reference,
                seq,
                kind,
                account,
                str(amount),
                requester_name,
                requester_role,
                requester_origin,
                state,
            ),
        )

    def get_request(self, reference: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()

    def all_requests_in_arrival_order(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM requests ORDER BY seq").fetchall()

    def set_request_state(self, reference: str, state: str) -> None:
        self.conn.execute(
            "UPDATE requests SET state = ? WHERE reference = ?", (state, reference)
        )

    def set_request_next_step(self, reference: str, next_step_index: int) -> None:
        self.conn.execute(
            "UPDATE requests SET next_step_index = ? WHERE reference = ?",
            (next_step_index, reference),
        )

    # -- steps --------------------------------------------------------

    def insert_step(self, reference: str, step_index: int, operation: str, state: str) -> None:
        self.conn.execute(
            "INSERT INTO steps (request_reference, step_index, operation, state) "
            "VALUES (?, ?, ?, ?)",
            (reference, step_index, operation, state),
        )

    def set_step_state(self, reference: str, step_index: int, state: str) -> None:
        self.conn.execute(
            "UPDATE steps SET state = ? WHERE request_reference = ? AND step_index = ?",
            (state, reference, step_index),
        )

    def steps_for_request(self, reference: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM steps WHERE request_reference = ? ORDER BY step_index",
            (reference,),
        ).fetchall()

    # -- approvals ------------------------------------------------------

    def insert_approval(
        self, reference: str, step_index: int, role_required: str
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO approvals (request_reference, step_index, role_required, status, decided_by_role) "
            "VALUES (?, ?, ?, 'pending', NULL)",
            (reference, step_index, role_required),
        )
        return int(cursor.lastrowid)

    def pending_approval_for_request(self, reference: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE request_reference = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (reference,),
        ).fetchone()

    def resolve_approval(self, approval_id: int, status: str, decided_by_role: str) -> None:
        self.conn.execute(
            "UPDATE approvals SET status = ?, decided_by_role = ? WHERE id = ?",
            (status, decided_by_role, approval_id),
        )

    def all_pending_approvals(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY id"
        ).fetchall()

    def approvals_for_request(self, reference: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE request_reference = ? ORDER BY id",
            (reference,),
        ).fetchall()

    # -- log --------------------------------------------------------

    def append_log(self, entry_type: str, reference: Optional[str], data: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO log_entries (type, request_reference, data) VALUES (?, ?, ?)",
            (entry_type, reference, json.dumps(data, sort_keys=True)),
        )

    def log_for_request(self, reference: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM log_entries WHERE request_reference = ? ORDER BY seq",
            (reference,),
        ).fetchall()

    def all_log_entries(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM log_entries ORDER BY seq").fetchall()

    def commit(self) -> None:
        self.conn.commit()

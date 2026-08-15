"""Persistent SQLite storage: the append-only log plus current state tables.

The log table is never updated or deleted from. State tables (accounts,
requests, steps, approvals) are the current materialized view used to run the
engine efficiently and to answer `show`/`export`/API queries without replaying
the whole log each time.
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

DEFAULT_DB_PATH = "log.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    reference TEXT,
    data TEXT NOT NULL
);

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
    current_step_index INTEGER NOT NULL,
    arrival_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (reference, step_index)
);

CREATE TABLE IF NOT EXISTS approvals (
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    required_role TEXT NOT NULL,
    status TEXT NOT NULL,
    resolved_role TEXT,
    decision TEXT,
    PRIMARY KEY (reference, step_index)
);
"""


class Database:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._seq = self._load_seq()

    def _load_seq(self) -> int:
        row = self.conn.execute("SELECT MAX(seq) AS m FROM log").fetchone()
        return (row["m"] or 0)

    def close(self) -> None:
        self.conn.close()

    def reset(self) -> None:
        """Drop and recreate every table, for a clean `run`."""
        self.conn.executescript(
            """
            DROP TABLE IF EXISTS log;
            DROP TABLE IF EXISTS accounts;
            DROP TABLE IF EXISTS requests;
            DROP TABLE IF EXISTS steps;
            DROP TABLE IF EXISTS approvals;
            """
        )
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._seq = 0

    # -- log -----------------------------------------------------------

    def append_log(self, event_type: str, reference: str | None, data: dict) -> None:
        self._seq += 1
        self.conn.execute(
            "INSERT INTO log (seq, event_type, reference, data) VALUES (?, ?, ?, ?)",
            (self._seq, event_type, reference, json.dumps(data, sort_keys=True)),
        )

    def log_for(self, reference: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT seq, event_type, reference, data FROM log WHERE reference = ? ORDER BY seq",
            (reference,),
        ).fetchall()
        return [
            {
                "seq": row["seq"],
                "event_type": row["event_type"],
                "reference": row["reference"],
                "data": json.loads(row["data"]),
            }
            for row in rows
        ]

    def all_log(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT seq, event_type, reference, data FROM log ORDER BY seq"
        ).fetchall()
        return [
            {
                "seq": row["seq"],
                "event_type": row["event_type"],
                "reference": row["reference"],
                "data": json.loads(row["data"]),
            }
            for row in rows
        ]

    # -- accounts --------------------------------------------------------

    def upsert_account(self, id: str, owner: str, tier: str, balance: Decimal, frozen: bool) -> None:
        self.conn.execute(
            "INSERT INTO accounts (id, owner, tier, balance, frozen) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET owner=excluded.owner, tier=excluded.tier,"
            " balance=excluded.balance, frozen=excluded.frozen",
            (id, owner, tier, str(balance), int(frozen)),
        )

    def get_account(self, id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM accounts WHERE id = ?", (id,)).fetchone()

    def all_accounts(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()

    # -- requests --------------------------------------------------------

    def insert_request(
        self,
        reference: str,
        kind: str,
        account: str,
        amount: str,
        requester_name: str,
        requester_role: str,
        requester_origin: str,
        arrival_order: int,
    ) -> None:
        self.conn.execute(
            "INSERT INTO requests (reference, kind, account, amount, requester_name,"
            " requester_role, requester_origin, state, current_step_index, arrival_order)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'received', 0, ?)",
            (
                reference,
                kind,
                account,
                amount,
                requester_name,
                requester_role,
                requester_origin,
                arrival_order,
            ),
        )

    def get_request(self, reference: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()

    def all_requests_in_arrival_order(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM requests ORDER BY arrival_order").fetchall()

    def set_request_state(self, reference: str, state: str) -> None:
        self.conn.execute(
            "UPDATE requests SET state = ? WHERE reference = ?", (state, reference)
        )

    def set_request_step_index(self, reference: str, step_index: int) -> None:
        self.conn.execute(
            "UPDATE requests SET current_step_index = ? WHERE reference = ?",
            (step_index, reference),
        )

    # -- steps -------------------------------------------------------------

    def insert_step(self, reference: str, step_index: int, operation: str) -> None:
        self.conn.execute(
            "INSERT INTO steps (reference, step_index, operation, state)"
            " VALUES (?, ?, ?, 'pending')",
            (reference, step_index, operation),
        )

    def set_step_state(self, reference: str, step_index: int, state: str) -> None:
        self.conn.execute(
            "UPDATE steps SET state = ? WHERE reference = ? AND step_index = ?",
            (state, reference, step_index),
        )

    def steps_for(self, reference: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM steps WHERE reference = ? ORDER BY step_index", (reference,)
        ).fetchall()

    # -- approvals -----------------------------------------------------------

    def insert_approval(
        self, reference: str, step_index: int, required_role: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO approvals (reference, step_index, required_role, status)"
            " VALUES (?, ?, ?, 'pending')",
            (reference, step_index, required_role),
        )

    def resolve_approval(
        self, reference: str, step_index: int, resolved_role: str, decision: str
    ) -> None:
        self.conn.execute(
            "UPDATE approvals SET status = 'resolved', resolved_role = ?, decision = ?"
            " WHERE reference = ? AND step_index = ?",
            (resolved_role, decision, reference, step_index),
        )

    def pending_approval_for(self, reference: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE reference = ? AND status = 'pending'"
            " ORDER BY step_index LIMIT 1",
            (reference,),
        ).fetchone()

    def all_pending_approvals(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY reference, step_index"
        ).fetchall()

    def approvals_for(self, reference: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE reference = ? ORDER BY step_index", (reference,)
        ).fetchall()

    def commit(self) -> None:
        self.conn.commit()

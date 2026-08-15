from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('standard', 'premium')),
    balance TEXT NOT NULL,
    frozen INTEGER NOT NULL CHECK (frozen IN (0, 1))
);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    amount TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state TEXT NOT NULL,
    current_step INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id),
    position INTEGER NOT NULL,
    operation TEXT NOT NULL,
    arguments TEXT NOT NULL,
    state TEXT NOT NULL,
    UNIQUE(request_id, position)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id),
    step_id INTEGER NOT NULL REFERENCES steps(id),
    required_role TEXT NOT NULL,
    status TEXT NOT NULL,
    decided_by_role TEXT,
    decision TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests(id),
    event_type TEXT NOT NULL,
    data TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS event_log_no_update
BEFORE UPDATE ON event_log
BEGIN
    SELECT RAISE(ABORT, 'event log is append only');
END;

CREATE TRIGGER IF NOT EXISTS event_log_no_delete
BEFORE DELETE ON event_log
BEGIN
    SELECT RAISE(ABORT, 'event log is append only');
END;
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def add_event(
        self, request_id: int, event_type: str, data: dict[str, Any]
    ) -> None:
        self.connection.execute(
            "INSERT INTO event_log(request_id, event_type, data) VALUES (?, ?, ?)",
            (request_id, event_type, json.dumps(data, separators=(",", ":"))),
        )

    def request_by_reference(self, reference: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()

    def account(self, account_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()

    def pending_approval(self, request_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT approvals.*, steps.operation, steps.arguments, steps.position
            FROM approvals
            JOIN steps ON steps.id = approvals.step_id
            WHERE approvals.request_id = ? AND approvals.status = 'pending'
            """,
            (request_id,),
        ).fetchone()

    def request_detail(self, reference: str) -> dict[str, Any] | None:
        request = self.request_by_reference(reference)
        if request is None:
            return None
        request_id = request["id"]
        steps = self.connection.execute(
            """
            SELECT position, operation, arguments, state
            FROM steps WHERE request_id = ? ORDER BY position
            """,
            (request_id,),
        ).fetchall()
        approvals = self.connection.execute(
            """
            SELECT steps.position AS step, approvals.required_role,
                   approvals.status, approvals.decided_by_role, approvals.decision
            FROM approvals
            JOIN steps ON steps.id = approvals.step_id
            WHERE approvals.request_id = ? ORDER BY approvals.id
            """,
            (request_id,),
        ).fetchall()
        events = self.log_for_request(request_id)
        return {
            "reference": request["reference"],
            "kind": request["kind"],
            "state": request["state"],
            "account": request["account_id"],
            "amount": request["amount"],
            "requester": {
                "name": request["requester_name"],
                "role": request["requester_role"],
                "origin": request["requester_origin"],
            },
            "steps": [
                {
                    "position": row["position"],
                    "operation": row["operation"],
                    "arguments": json.loads(row["arguments"]),
                    "state": row["state"],
                }
                for row in steps
            ],
            "approvals": [dict(row) for row in approvals],
            "log": events,
        }

    def log_for_request(self, request_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT sequence, event_type, data
            FROM event_log WHERE request_id = ? ORDER BY sequence
            """,
            (request_id,),
        ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event": row["event_type"],
                "data": json.loads(row["data"]),
            }
            for row in rows
        ]

    def list_requests(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT reference, kind, state, account_id AS account, amount
            FROM requests ORDER BY id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    def list_pending_approvals(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT requests.reference, steps.position AS step, steps.operation,
                   approvals.required_role
            FROM approvals
            JOIN requests ON requests.id = approvals.request_id
            JOIN steps ON steps.id = approvals.step_id
            WHERE approvals.status = 'pending'
            ORDER BY approvals.id
            """
        ).fetchall()
        return [dict(row) for row in rows]

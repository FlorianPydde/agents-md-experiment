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
    reference TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    amount TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state TEXT NOT NULL,
    next_step INTEGER NOT NULL DEFAULT 0,
    arrival_order INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS request_steps (
    reference TEXT NOT NULL REFERENCES requests(reference),
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    arguments TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (reference, step_index)
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL REFERENCES requests(reference),
    step_index INTEGER NOT NULL,
    required_role TEXT NOT NULL,
    status TEXT NOT NULL,
    decided_by TEXT,
    decision TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_approval
    ON approvals(reference) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS event_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    event TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS event_log_no_update
    BEFORE UPDATE ON event_log
    BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS event_log_no_delete
    BEFORE DELETE ON event_log
    BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def reset(self) -> None:
        self.connection.close()
        self.path.unlink(missing_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def log(self, reference: str, event: str, data: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO event_log(reference, event, data) VALUES (?, ?, ?)",
            (reference, event, json.dumps(data, separators=(",", ":"), sort_keys=False)),
        )

    def request_detail(self, reference: str) -> dict[str, Any] | None:
        request = self.connection.execute(
            """SELECT reference, kind, account_id AS account, amount,
                      requester_name, requester_role, requester_origin, state
               FROM requests WHERE reference = ?""",
            (reference,),
        ).fetchone()
        if request is None:
            return None
        result = dict(request)
        result["requester"] = {
            "name": result.pop("requester_name"),
            "role": result.pop("requester_role"),
            "origin": result.pop("requester_origin"),
        }
        result["steps"] = [
            {
                "index": row["step_index"],
                "operation": row["operation"],
                "arguments": json.loads(row["arguments"]),
                "state": row["state"],
            }
            for row in self.connection.execute(
                """SELECT step_index, operation, arguments, state
                   FROM request_steps WHERE reference = ? ORDER BY step_index""",
                (reference,),
            )
        ]
        result["approvals"] = [
            dict(row)
            for row in self.connection.execute(
                """SELECT step_index, required_role, status, decided_by, decision
                   FROM approvals WHERE reference = ? ORDER BY id""",
                (reference,),
            )
        ]
        return result

    def log_entries(self, reference: str) -> list[dict[str, Any]]:
        return [
            {
                "sequence": row["sequence"],
                "event": row["event"],
                "data": json.loads(row["data"]),
            }
            for row in self.connection.execute(
                """SELECT sequence, event, data FROM event_log
                   WHERE reference = ? ORDER BY sequence""",
                (reference,),
            )
        ]

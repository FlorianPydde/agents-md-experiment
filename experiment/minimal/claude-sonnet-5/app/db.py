"""Persistence: a SQLite database holding accounts, requests, steps,
approvals, and the append only log.

The log is the durable record of every meaningful occurrence: a request
arriving, a plan being made, a policy decision, an approval being requested
or resolved, an operation running or failing, and a request reaching a final
state. Rows are only ever inserted, never updated or deleted.
"""

from __future__ import annotations

import json
import sqlite3
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
    next_step_index INTEGER NOT NULL DEFAULT 0,
    seq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    request_reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    args TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (request_reference, step_index)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    role_required TEXT NOT NULL,
    status TEXT NOT NULL,
    decided_role TEXT,
    decision TEXT
);

CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    request_reference TEXT,
    data TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def fresh_database(db_path: str | Path) -> sqlite3.Connection:
    """Start from a clean database: delete any existing file, then create it."""

    p = Path(db_path)
    if p.exists():
        p.unlink()
    conn = connect(p)
    init_schema(conn)
    return conn


def append_log(
    conn: sqlite3.Connection, event: str, reference: str | None, data: dict
) -> None:
    conn.execute(
        "INSERT INTO log (event, request_reference, data) VALUES (?, ?, ?)",
        (event, reference, json.dumps(data)),
    )
    conn.commit()


def log_entries_for(conn: sqlite3.Connection, reference: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, event, request_reference, data FROM log "
        "WHERE request_reference = ? ORDER BY id",
        (reference,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "event": row["event"],
            "reference": row["request_reference"],
            "data": json.loads(row["data"]),
        }
        for row in rows
    ]

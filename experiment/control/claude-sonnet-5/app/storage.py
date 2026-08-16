"""SQLite backed storage: the append only log plus current state.

The log records every meaningful occurrence and is never edited or
deleted. Current state (accounts, requests, steps, approvals) is held in
ordinary tables so the system can answer "what is true now" without
replaying the log; the log remains the record of "what happened".
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

DEFAULT_DB_PATH = "governed.db"

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
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    account TEXT NOT NULL,
    amount TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state TEXT NOT NULL,
    current_step INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_ref TEXT NOT NULL,
    idx INTEGER NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    UNIQUE (request_ref, idx)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_ref TEXT NOT NULL,
    step_idx INTEGER NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    decided_by_role TEXT,
    decision TEXT
);

CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seq INTEGER NOT NULL,
    request_ref TEXT,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def reset(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Start from a clean database: drop and recreate every table."""

    path = Path(db_path)
    if path.exists():
        path.unlink()
    return connect(path)


def next_seq(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM log").fetchone()
    return row["n"]


def append_log(
    conn: sqlite3.Connection,
    event_type: str,
    data: dict,
    request_ref: str | None = None,
) -> None:
    seq = next_seq(conn)
    conn.execute(
        "INSERT INTO log (seq, request_ref, event_type, data, occurred_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            seq,
            request_ref,
            event_type,
            json.dumps(data, sort_keys=True, default=_json_default),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serialisable: {value!r}")


def load_world(conn: sqlite3.Connection, world: dict) -> None:
    for account in world["accounts"]:
        conn.execute(
            "INSERT INTO accounts (id, owner, tier, balance, frozen)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                account["id"],
                account["owner"],
                account["tier"],
                str(account["balance"]),
                1 if account["frozen"] else 0,
            ),
        )

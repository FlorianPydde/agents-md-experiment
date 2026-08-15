"""Persistence.

The log table is append only: rows are inserted and never updated or deleted.
Alongside it the store keeps the current projection of requests, steps,
approvals and accounts so that `show`, `export` and `serve` can work against a
database written by an earlier `run`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Account,
    Approval,
    ApprovalState,
    Kind,
    Origin,
    Request,
    Requester,
    Role,
    State,
    Step,
    StepState,
    Tier,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    event      TEXT NOT NULL,
    reference  TEXT,
    data       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    reference       TEXT PRIMARY KEY,
    arrival         INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    account         TEXT NOT NULL,
    amount          TEXT NOT NULL,
    requester_name  TEXT NOT NULL,
    requester_role  TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    reference  TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    operation  TEXT NOT NULL,
    state      TEXT NOT NULL,
    detail     TEXT,
    PRIMARY KEY (reference, idx)
);

CREATE TABLE IF NOT EXISTS approvals (
    reference     TEXT NOT NULL,
    idx           INTEGER NOT NULL,
    operation     TEXT NOT NULL,
    required_role TEXT NOT NULL,
    state         TEXT NOT NULL,
    decided_by    TEXT,
    PRIMARY KEY (reference, idx)
);

CREATE TABLE IF NOT EXISTS accounts (
    id      TEXT PRIMARY KEY,
    owner   TEXT NOT NULL,
    tier    TEXT NOT NULL,
    balance TEXT NOT NULL,
    frozen  INTEGER NOT NULL
);

CREATE TRIGGER IF NOT EXISTS log_is_append_only_update
BEFORE UPDATE ON log
BEGIN
    SELECT RAISE(ABORT, 'the log is append only');
END;

CREATE TRIGGER IF NOT EXISTS log_is_append_only_delete
BEFORE DELETE ON log
BEGIN
    SELECT RAISE(ABORT, 'the log is append only');
END;
"""

DEFAULT_DB_NAME = "runner.db"


@dataclass(frozen=True)
class LogEntry:
    seq: int
    event: str
    reference: str | None
    data: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event": self.event,
            "reference": self.reference,
            "data": self.data,
        }


class Store:
    """A SQLite backed store holding the append only log and current state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @classmethod
    def fresh(cls, path: Path) -> "Store":
        """Open a store after removing any previous database file."""
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                candidate.unlink()
        return cls(path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- log

    def append(self, event: str, reference: str | None, data: dict[str, Any]) -> int:
        cursor = self._conn.execute(
            "INSERT INTO log (event, reference, data) VALUES (?, ?, ?)",
            (event, reference, json.dumps(data, sort_keys=False)),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def log_entries(self, reference: str | None = None) -> list[LogEntry]:
        if reference is None:
            rows = self._conn.execute("SELECT * FROM log ORDER BY seq").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM log WHERE reference = ? ORDER BY seq", (reference,)
            ).fetchall()
        return [
            LogEntry(row["seq"], row["event"], row["reference"], json.loads(row["data"]))
            for row in rows
        ]

    # -------------------------------------------------------------- state

    def save_request(self, request: Request) -> None:
        self._conn.execute(
            """INSERT INTO requests
               (reference, arrival, kind, account, amount, requester_name,
                requester_role, requester_origin, state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(reference) DO UPDATE SET state = excluded.state""",
            (
                request.reference,
                request.arrival,
                request.kind.value,
                request.account,
                format(request.amount, ".2f"),
                request.requester.name,
                request.requester.role,
                request.requester.origin.value,
                request.state.value,
            ),
        )
        self._conn.execute("DELETE FROM steps WHERE reference = ?", (request.reference,))
        self._conn.executemany(
            "INSERT INTO steps (reference, idx, operation, state, detail) VALUES (?, ?, ?, ?, ?)",
            [
                (request.reference, step.index, step.operation, step.state.value, step.detail)
                for step in request.steps
            ],
        )
        self._conn.execute("DELETE FROM approvals WHERE reference = ?", (request.reference,))
        self._conn.executemany(
            """INSERT INTO approvals (reference, idx, operation, required_role, state, decided_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    request.reference,
                    approval.step_index,
                    approval.operation,
                    approval.required_role.value,
                    approval.state.value,
                    approval.decided_by.value if approval.decided_by else None,
                )
                for approval in request.approvals
            ],
        )
        self._conn.commit()

    def save_accounts(self, accounts: Iterable[Account]) -> None:
        self._conn.executemany(
            """INSERT INTO accounts (id, owner, tier, balance, frozen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 owner = excluded.owner, tier = excluded.tier,
                 balance = excluded.balance, frozen = excluded.frozen""",
            [
                (a.id, a.owner, a.tier.value, format(a.balance, ".2f"), int(a.frozen))
                for a in accounts
            ],
        )
        self._conn.commit()

    def load_accounts(self) -> list[Account]:
        rows = self._conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [
            Account(
                id=row["id"],
                owner=row["owner"],
                tier=Tier(row["tier"]),
                balance=Decimal(row["balance"]),
                frozen=bool(row["frozen"]),
            )
            for row in rows
        ]

    def load_requests(self) -> list[Request]:
        rows = self._conn.execute("SELECT * FROM requests ORDER BY arrival").fetchall()
        requests = []
        for row in rows:
            request = Request(
                reference=row["reference"],
                kind=Kind(row["kind"]),
                account=row["account"],
                amount=Decimal(row["amount"]),
                requester=Requester(
                    name=row["requester_name"],
                    role=row["requester_role"],
                    origin=Origin(row["requester_origin"]),
                ),
                arrival=row["arrival"],
                state=State(row["state"]),
            )
            request.steps = [
                Step(
                    index=step["idx"],
                    operation=step["operation"],
                    state=StepState(step["state"]),
                    detail=step["detail"],
                )
                for step in self._conn.execute(
                    "SELECT * FROM steps WHERE reference = ? ORDER BY idx", (request.reference,)
                )
            ]
            request.approvals = [
                Approval(
                    reference=request.reference,
                    step_index=approval["idx"],
                    operation=approval["operation"],
                    required_role=Role(approval["required_role"]),
                    state=ApprovalState(approval["state"]),
                    decided_by=Role(approval["decided_by"]) if approval["decided_by"] else None,
                )
                for approval in self._conn.execute(
                    "SELECT * FROM approvals WHERE reference = ? ORDER BY idx",
                    (request.reference,),
                )
            ]
            requests.append(request)
        return requests

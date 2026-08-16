"""Persistence.

Everything the system knows lives in one SQLite database in this folder:
the accounts, the requests and their steps, the approvals, and the log.

The log table is append only.  Triggers in the schema refuse updates and
deletes, so the guarantee does not depend on callers behaving.
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import (
    Account,
    Approval,
    Decision,
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

DATABASE_NAME = "ledger.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id      TEXT PRIMARY KEY,
    owner   TEXT NOT NULL,
    tier    TEXT NOT NULL,
    balance TEXT NOT NULL,
    frozen  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    reference      TEXT PRIMARY KEY,
    arrival        INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    account        TEXT NOT NULL,
    amount         TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    origin         TEXT NOT NULL,
    state          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    reference  TEXT NOT NULL,
    position   INTEGER NOT NULL,
    operation  TEXT NOT NULL,
    arguments  TEXT NOT NULL,
    state      TEXT NOT NULL,
    PRIMARY KEY (reference, position)
);

CREATE TABLE IF NOT EXISTS approvals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    reference  TEXT NOT NULL,
    position   INTEGER NOT NULL,
    operation  TEXT NOT NULL,
    role       TEXT NOT NULL,
    state      TEXT NOT NULL,
    decision   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT,
    type      TEXT NOT NULL,
    data      TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS events_are_append_only_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'the log is append only');
END;

CREATE TRIGGER IF NOT EXISTS events_are_append_only_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'the log is append only');
END;
"""


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    raise TypeError(f"cannot store {type(value).__name__} in the log")


class Store:
    """The database, and the reads and writes the rest of the program needs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    # -- lifecycle -----------------------------------------------------
    @classmethod
    def fresh(cls, path: Path) -> "Store":
        """A database with nothing in it, replacing any earlier one."""
        path.unlink(missing_ok=True)
        return cls(path)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- accounts ------------------------------------------------------
    def put_accounts(self, accounts: list[Account]) -> None:
        self.connection.executemany(
            "INSERT INTO accounts (id, owner, tier, balance, frozen) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET owner=excluded.owner, tier=excluded.tier,"
            " balance=excluded.balance, frozen=excluded.frozen",
            [
                (a.id, a.owner, str(a.tier), f"{a.balance:.2f}", int(a.frozen))
                for a in accounts
            ],
        )
        self.connection.commit()

    def accounts(self) -> list[Account]:
        rows = self.connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
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

    # -- requests ------------------------------------------------------
    def put_request(self, request: Request, arrival: int, state: State) -> None:
        self.connection.execute(
            "INSERT INTO requests (reference, arrival, kind, account, amount,"
            " requester_name, requester_role, origin, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.reference,
                arrival,
                str(request.kind),
                request.account,
                f"{request.amount:.2f}",
                request.requester.name,
                request.requester.role,
                str(request.requester.origin),
                str(state),
            ),
        )
        self.connection.commit()

    def set_request_state(self, reference: str, state: State) -> None:
        self.connection.execute(
            "UPDATE requests SET state = ? WHERE reference = ?", (str(state), reference)
        )
        self.connection.commit()

    def request(self, reference: str) -> tuple[Request, State] | None:
        row = self.connection.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()
        return _row_to_request(row) if row else None

    def requests(self) -> list[tuple[Request, State]]:
        rows = self.connection.execute("SELECT * FROM requests ORDER BY arrival").fetchall()
        return [_row_to_request(row) for row in rows]

    # -- steps ---------------------------------------------------------
    def put_steps(self, reference: str, steps: list[Step]) -> None:
        self.connection.executemany(
            "INSERT INTO steps (reference, position, operation, arguments, state)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (
                    reference,
                    step.index,
                    step.operation,
                    json.dumps(step.arguments, default=_encode),
                    str(StepState.PENDING),
                )
                for step in steps
            ],
        )
        self.connection.commit()

    def set_step_state(self, reference: str, position: int, state: StepState) -> None:
        self.connection.execute(
            "UPDATE steps SET state = ? WHERE reference = ? AND position = ?",
            (str(state), reference, position),
        )
        self.connection.commit()

    def steps(self, reference: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM steps WHERE reference = ? ORDER BY position", (reference,)
        ).fetchall()
        return [
            {
                "position": row["position"],
                "operation": row["operation"],
                "arguments": json.loads(row["arguments"]),
                "state": row["state"],
            }
            for row in rows
        ]

    # -- approvals -----------------------------------------------------
    def put_approval(self, reference: str, position: int, operation: str, role: Role) -> None:
        self.connection.execute(
            "INSERT INTO approvals (reference, position, operation, role, state, decision)"
            " VALUES (?, ?, ?, ?, 'pending', NULL)",
            (reference, position, operation, str(role)),
        )
        self.connection.commit()

    def pending_approval(self, reference: str) -> Approval | None:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE reference = ? AND state = 'pending'"
            " ORDER BY id LIMIT 1",
            (reference,),
        ).fetchone()
        return _row_to_approval(row) if row else None

    def pending_approvals(self) -> list[Approval]:
        rows = self.connection.execute(
            "SELECT * FROM approvals WHERE state = 'pending' ORDER BY id"
        ).fetchall()
        return [_row_to_approval(row) for row in rows]

    def approvals(self, reference: str) -> list[Approval]:
        rows = self.connection.execute(
            "SELECT * FROM approvals WHERE reference = ? ORDER BY id", (reference,)
        ).fetchall()
        return [_row_to_approval(row) for row in rows]

    def resolve_approval(self, reference: str, position: int, decision: Decision) -> None:
        self.connection.execute(
            "UPDATE approvals SET state = ?, decision = ?"
            " WHERE reference = ? AND position = ? AND state = 'pending'",
            (
                "approved" if decision is Decision.APPROVE else "rejected",
                str(decision),
                reference,
                position,
            ),
        )
        self.connection.commit()

    # -- log -----------------------------------------------------------
    def append_event(self, kind: str, reference: str | None, data: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO events (reference, type, data) VALUES (?, ?, ?)",
            (reference, kind, json.dumps(data, default=_encode)),
        )
        self.connection.commit()

    def events(self, reference: str | None = None) -> list[dict[str, Any]]:
        if reference is None:
            rows = self.connection.execute("SELECT * FROM events ORDER BY id").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM events WHERE reference = ? ORDER BY id", (reference,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "reference": row["reference"],
                "type": row["type"],
                "data": json.loads(row["data"]),
            }
            for row in rows
        ]


def _row_to_request(row: sqlite3.Row) -> tuple[Request, State]:
    request = Request(
        reference=row["reference"],
        kind=Kind(row["kind"]),
        account=row["account"],
        amount=Decimal(row["amount"]),
        requester=Requester(
            name=row["requester_name"],
            role=row["requester_role"],
            origin=Origin(row["origin"]),
        ),
    )
    return request, State(row["state"])


def _row_to_approval(row: sqlite3.Row) -> Approval:
    return Approval(
        reference=row["reference"],
        step_index=row["position"],
        operation=row["operation"],
        role=Role(row["role"]),
        state=row["state"],
        decision=row["decision"],
    )

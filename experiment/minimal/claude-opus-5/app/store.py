"""Persistence. The log table is append only and enforced as such by the database."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from .domain import (
    Account,
    Approval,
    ApprovalState,
    Event,
    EventType,
    OperationName,
    Origin,
    Requester,
    RequestKind,
    RequestRecord,
    RequestState,
    Role,
    ServiceRequest,
    Step,
    StepState,
    Tier,
)
from .money import format_amount

DEFAULT_DATABASE = Path(__file__).resolve().parent.parent / "ledger.db"

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
    arrival INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    arguments TEXT NOT NULL,
    state TEXT NOT NULL,
    required_role TEXT,
    PRIMARY KEY (reference, step_index)
);

CREATE TABLE IF NOT EXISTS approvals (
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    role TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (reference, step_index)
);

CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    type TEXT NOT NULL,
    details TEXT NOT NULL
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


def _details_to_json(details: tuple[tuple[str, str], ...]) -> str:
    return json.dumps([[key, value] for key, value in details])


def _details_from_json(raw: str) -> tuple[tuple[str, str], ...]:
    return tuple((str(key), str(value)) for key, value in json.loads(raw))


class Store:
    """Reads and writes every durable part of the system."""

    def __init__(self, path: Path, *, reset: bool = False) -> None:
        self.path = path
        if reset and path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # accounts -----------------------------------------------------------

    def save_accounts(self, accounts: Iterable[Account]) -> None:
        self._connection.executemany(
            "INSERT INTO accounts (id, owner, tier, balance, frozen) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET owner=excluded.owner, tier=excluded.tier, "
            "balance=excluded.balance, frozen=excluded.frozen",
            [
                (
                    account.id,
                    account.owner,
                    account.tier.value,
                    format_amount(account.balance),
                    int(account.frozen),
                )
                for account in accounts
            ],
        )
        self._connection.commit()

    def accounts(self) -> tuple[Account, ...]:
        rows = self._connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return tuple(
            Account(
                id=row["id"],
                owner=row["owner"],
                tier=Tier(row["tier"]),
                balance=Decimal(row["balance"]),
                frozen=bool(row["frozen"]),
            )
            for row in rows
        )

    # requests -----------------------------------------------------------

    def save_record(self, record: RequestRecord) -> None:
        request = record.request
        self._connection.execute(
            "INSERT INTO requests (reference, kind, account, amount, requester_name, "
            "requester_role, requester_origin, state, arrival) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(reference) DO UPDATE SET state=excluded.state",
            (
                request.reference,
                request.kind.value,
                request.account,
                format_amount(request.amount),
                request.requester.name,
                request.requester.role,
                request.requester.origin.value,
                record.state.value,
                record.arrival,
            ),
        )
        self._connection.executemany(
            "INSERT INTO steps (reference, step_index, operation, arguments, state, required_role) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(reference, step_index) DO UPDATE SET "
            "state=excluded.state, required_role=excluded.required_role",
            [
                (
                    request.reference,
                    step.index,
                    step.operation.value,
                    json.dumps(step.arguments),
                    step.state.value,
                    step.required_role.value if step.required_role else None,
                )
                for step in record.steps
            ],
        )
        self._connection.commit()

    def _record_from_rows(self, request_row: sqlite3.Row) -> RequestRecord:
        step_rows = self._connection.execute(
            "SELECT * FROM steps WHERE reference = ? ORDER BY step_index",
            (request_row["reference"],),
        ).fetchall()
        return RequestRecord(
            request=ServiceRequest(
                reference=request_row["reference"],
                kind=RequestKind(request_row["kind"]),
                account=request_row["account"],
                amount=Decimal(request_row["amount"]),
                requester=Requester(
                    name=request_row["requester_name"],
                    role=request_row["requester_role"],
                    origin=Origin(request_row["requester_origin"]),
                ),
            ),
            steps=tuple(
                Step(
                    index=row["step_index"],
                    operation=OperationName(row["operation"]),
                    arguments=json.loads(row["arguments"]),
                    state=StepState(row["state"]),
                    required_role=Role(row["required_role"]) if row["required_role"] else None,
                )
                for row in step_rows
            ),
            state=RequestState(request_row["state"]),
            arrival=request_row["arrival"],
        )

    def records(self) -> tuple[RequestRecord, ...]:
        rows = self._connection.execute("SELECT * FROM requests ORDER BY arrival").fetchall()
        return tuple(self._record_from_rows(row) for row in rows)

    def record(self, reference: str) -> RequestRecord | None:
        row = self._connection.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()
        return None if row is None else self._record_from_rows(row)

    # approvals ----------------------------------------------------------

    def save_approval(self, approval: Approval) -> None:
        self._connection.execute(
            "INSERT INTO approvals (reference, step_index, operation, role, state) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(reference, step_index) DO UPDATE SET "
            "state=excluded.state",
            (
                approval.reference,
                approval.step_index,
                approval.operation.value,
                approval.role.value,
                approval.state.value,
            ),
        )
        self._connection.commit()

    def _approvals(self, sql: str, parameters: tuple[object, ...]) -> tuple[Approval, ...]:
        rows = self._connection.execute(sql, parameters).fetchall()
        return tuple(
            Approval(
                reference=row["reference"],
                step_index=row["step_index"],
                operation=OperationName(row["operation"]),
                role=Role(row["role"]),
                state=ApprovalState(row["state"]),
            )
            for row in rows
        )

    def approvals_for(self, reference: str) -> tuple[Approval, ...]:
        return self._approvals(
            "SELECT * FROM approvals WHERE reference = ? ORDER BY step_index", (reference,)
        )

    def pending_approvals(self) -> tuple[Approval, ...]:
        return self._approvals(
            "SELECT * FROM approvals WHERE state = ? ORDER BY reference, step_index",
            (ApprovalState.PENDING.value,),
        )

    def pending_approval(self, reference: str) -> Approval | None:
        found = self._approvals(
            "SELECT * FROM approvals WHERE reference = ? AND state = ? ORDER BY step_index",
            (reference, ApprovalState.PENDING.value),
        )
        return found[0] if found else None

    # log ----------------------------------------------------------------

    def append_event(
        self, reference: str, event_type: EventType, details: tuple[tuple[str, str], ...]
    ) -> Event:
        cursor = self._connection.execute(
            "INSERT INTO events (reference, type, details) VALUES (?, ?, ?)",
            (reference, event_type.value, _details_to_json(details)),
        )
        self._connection.commit()
        return Event(
            sequence=int(cursor.lastrowid or 0),
            reference=reference,
            type=event_type,
            details=details,
        )

    def events_for(self, reference: str) -> tuple[Event, ...]:
        rows = self._connection.execute(
            "SELECT * FROM events WHERE reference = ? ORDER BY sequence", (reference,)
        ).fetchall()
        return tuple(
            Event(
                sequence=row["sequence"],
                reference=row["reference"],
                type=EventType(row["type"]),
                details=_details_from_json(row["details"]),
            )
            for row in rows
        )

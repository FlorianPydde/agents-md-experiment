"""SQLite persistence: accounts, requests, steps, approvals, and the append only log."""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from app.domain import (
    Account,
    Approval,
    Money,
    Requester,
    ServiceRequest,
    StepRecord,
    TrackedRequest,
)
from app.enums import (
    ApprovalState,
    EventKind,
    OperationName,
    Origin,
    RequestKind,
    RequestState,
    Role,
    StepState,
    Tier,
)
from app.errors import ServiceRequestError, UnknownReferenceError
from app.events import LogEntry, RecordedEntry
from app.plans import Step

DEFAULT_DATABASE = Path("service_log.db")

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
    arrival INTEGER NOT NULL,
    kind TEXT NOT NULL,
    account TEXT NOT NULL,
    amount TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    reference TEXT NOT NULL,
    position INTEGER NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (reference, position)
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    position INTEGER NOT NULL,
    role TEXT NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    reference TEXT NOT NULL,
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


class Store:
    """The database. Everything the runner remembers between processes lives here."""

    def __init__(self, path: Path = DEFAULT_DATABASE) -> None:
        self.path = path
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def start_clean(self, accounts: tuple[Account, ...]) -> None:
        """Drop everything, including the log, and load the starting world."""
        self._connection.executescript(
            "DROP TRIGGER IF EXISTS events_are_append_only_update;"
            "DROP TRIGGER IF EXISTS events_are_append_only_delete;"
            "DROP TABLE IF EXISTS events;"
            "DROP TABLE IF EXISTS approvals;"
            "DROP TABLE IF EXISTS steps;"
            "DROP TABLE IF EXISTS requests;"
            "DROP TABLE IF EXISTS accounts;"
        )
        self._connection.executescript(SCHEMA)
        for account in accounts:
            self.save_account(account)
        self._connection.commit()

    # accounts

    def save_account(self, account: Account) -> None:
        self._connection.execute(
            "INSERT INTO accounts (id, owner, tier, balance, frozen) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "owner=excluded.owner, tier=excluded.tier, balance=excluded.balance, "
            "frozen=excluded.frozen",
            (
                account.id,
                account.owner,
                account.tier.value,
                str(account.balance),
                int(account.frozen),
            ),
        )
        self._connection.commit()

    def account(self, account_id: str) -> Account:
        row = self._connection.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if row is None:
            raise UnknownReferenceError(f"unknown account {account_id}")
        return _account_from(row)

    def accounts(self) -> tuple[Account, ...]:
        rows = self._connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return tuple(_account_from(row) for row in rows)

    # requests

    def add_request(self, request: ServiceRequest) -> None:
        if self._connection.execute(
            "SELECT 1 FROM requests WHERE reference = ?", (request.reference,)
        ).fetchone():
            raise ServiceRequestError(f"request {request.reference} arrived twice")
        arrival = self._connection.execute(
            "SELECT COALESCE(MAX(arrival), 0) + 1 AS next FROM requests"
        ).fetchone()["next"]
        self._connection.execute(
            "INSERT INTO requests (reference, arrival, kind, account, amount, "
            "requester_name, requester_role, requester_origin, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.reference,
                arrival,
                request.kind.value,
                request.account,
                str(request.amount),
                request.requester.name,
                request.requester.role,
                request.requester.origin.value,
                RequestState.RECEIVED.value,
            ),
        )
        self._connection.commit()

    def tracked(self, reference: str) -> TrackedRequest:
        row = self._connection.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()
        if row is None:
            raise UnknownReferenceError(f"unknown request {reference}")
        return _tracked_from(row)

    def tracked_requests(self) -> tuple[TrackedRequest, ...]:
        rows = self._connection.execute(
            "SELECT * FROM requests ORDER BY arrival"
        ).fetchall()
        return tuple(_tracked_from(row) for row in rows)

    def set_request_state(self, reference: str, state: RequestState) -> None:
        self._connection.execute(
            "UPDATE requests SET state = ? WHERE reference = ?",
            (state.value, reference),
        )
        self._connection.commit()

    # steps

    def save_plan(self, reference: str, steps: tuple[Step, ...]) -> None:
        self._connection.executemany(
            "INSERT INTO steps (reference, position, operation, state) "
            "VALUES (?, ?, ?, ?)",
            [
                (reference, step.position, step.operation.value, step.state.value)
                for step in steps
            ],
        )
        self._connection.commit()

    def step_records(self, reference: str) -> tuple[StepRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM steps WHERE reference = ? ORDER BY position", (reference,)
        ).fetchall()
        return tuple(
            StepRecord(
                position=row["position"],
                operation=OperationName(row["operation"]),
                state=StepState(row["state"]),
            )
            for row in rows
        )

    def set_step_state(self, reference: str, position: int, state: StepState) -> None:
        self._connection.execute(
            "UPDATE steps SET state = ? WHERE reference = ? AND position = ?",
            (state.value, reference, position),
        )
        self._connection.commit()

    # approvals

    def add_approval(self, reference: str, position: int, role: Role) -> None:
        self._connection.execute(
            "INSERT INTO approvals (reference, position, role, state) "
            "VALUES (?, ?, ?, ?)",
            (reference, position, role.value, ApprovalState.PENDING.value),
        )
        self._connection.commit()

    def pending_approval(self, reference: str) -> Approval | None:
        row = self._connection.execute(
            "SELECT * FROM approvals WHERE reference = ? AND state = ? "
            "ORDER BY id LIMIT 1",
            (reference, ApprovalState.PENDING.value),
        ).fetchone()
        return None if row is None else _approval_from(row)

    def pending_approvals(self) -> tuple[Approval, ...]:
        rows = self._connection.execute(
            "SELECT * FROM approvals WHERE state = ? ORDER BY id",
            (ApprovalState.PENDING.value,),
        ).fetchall()
        return tuple(_approval_from(row) for row in rows)

    def approvals(self, reference: str) -> tuple[Approval, ...]:
        rows = self._connection.execute(
            "SELECT * FROM approvals WHERE reference = ? ORDER BY id", (reference,)
        ).fetchall()
        return tuple(_approval_from(row) for row in rows)

    def settle_approval(self, approval: Approval, state: ApprovalState) -> None:
        self._connection.execute(
            "UPDATE approvals SET state = ? WHERE reference = ? AND position = ? "
            "AND state = ?",
            (
                state.value,
                approval.reference,
                approval.position,
                ApprovalState.PENDING.value,
            ),
        )
        self._connection.commit()

    # log

    def append(self, entry: LogEntry) -> None:
        self._connection.execute(
            "INSERT INTO events (kind, reference, details) VALUES (?, ?, ?)",
            (entry.kind.value, entry.reference, json.dumps(entry.details)),
        )
        self._connection.commit()

    def entries(self, reference: str) -> tuple[RecordedEntry, ...]:
        rows = self._connection.execute(
            "SELECT * FROM events WHERE reference = ? ORDER BY id", (reference,)
        ).fetchall()
        return tuple(_entry_from(row) for row in rows)

    def all_entries(self) -> tuple[RecordedEntry, ...]:
        rows = self._connection.execute("SELECT * FROM events ORDER BY id").fetchall()
        return tuple(_entry_from(row) for row in rows)


def _account_from(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        owner=row["owner"],
        tier=Tier(row["tier"]),
        balance=Money(Decimal(row["balance"])),
        frozen=bool(row["frozen"]),
    )


def _tracked_from(row: sqlite3.Row) -> TrackedRequest:
    return TrackedRequest(
        request=ServiceRequest(
            reference=row["reference"],
            kind=RequestKind(row["kind"]),
            account=row["account"],
            amount=Money(Decimal(row["amount"])),
            requester=Requester(
                name=row["requester_name"],
                role=row["requester_role"],
                origin=Origin(row["requester_origin"]),
            ),
        ),
        state=RequestState(row["state"]),
    )


def _approval_from(row: sqlite3.Row) -> Approval:
    return Approval(
        reference=row["reference"],
        position=row["position"],
        role=Role(row["role"]),
        state=ApprovalState(row["state"]),
    )


def _entry_from(row: sqlite3.Row) -> RecordedEntry:
    details = tuple((name, value) for name, value in json.loads(row["details"]))
    return RecordedEntry(
        position=row["id"],
        entry=LogEntry(
            kind=EventKind(row["kind"]), reference=row["reference"], details=details
        ),
    )

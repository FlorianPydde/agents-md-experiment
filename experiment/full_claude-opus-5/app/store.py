"""SQLite-backed persistence: accounts, requests, steps, approvals, and the
append only log. This module is the only place that touches SQL; everything
it returns or accepts is already a domain type (rule 10).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.domain import (
    Account,
    AccountId,
    ApprovalRecord,
    ApproverRole,
    Decision,
    LogEntry,
    LogEntryType,
    Money,
    OperationName,
    Origin,
    Reference,
    Requester,
    RequestKind,
    RequestRecord,
    RequestState,
    ServiceRequest,
    StepRecord,
    StepStatus,
    Tier,
)

_SCHEMA = """
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
    account_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state TEXT NOT NULL,
    current_step_index INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (reference, step_index)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    operation TEXT NOT NULL,
    required_role TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    decision TEXT,
    resolved_by_role TEXT
);

CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


class Store:
    """A connection to the append only log and current state database."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- accounts ---------------------------------------------------------

    def put_account(self, account: Account) -> None:
        self._conn.execute(
            """
            INSERT INTO accounts (id, owner, tier, balance, frozen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                owner = excluded.owner,
                tier = excluded.tier,
                balance = excluded.balance,
                frozen = excluded.frozen
            """,
            (account.id, account.owner, account.tier.value, account.balance.formatted(), int(account.frozen)),
        )
        self._conn.commit()

    def get_account(self, account_id: AccountId) -> Account | None:
        row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        return _account_from_row(row)

    def list_accounts(self) -> list[Account]:
        rows = self._conn.execute("SELECT * FROM accounts ORDER BY id ASC").fetchall()
        return [_account_from_row(row) for row in rows]

    # -- requests -----------------------------------------------------------

    def put_request(self, request: ServiceRequest, state: RequestState, current_step_index: int, seq: int) -> None:
        self._conn.execute(
            """
            INSERT INTO requests
                (reference, seq, kind, account_id, amount, requester_name, requester_role,
                 requester_origin, state, current_step_index)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.reference,
                seq,
                request.kind.value,
                request.account_id,
                request.amount.formatted(),
                request.requester.name,
                request.requester.role,
                request.requester.origin.value,
                state.value,
                current_step_index,
            ),
        )
        self._conn.commit()

    def update_request(self, reference: Reference, state: RequestState, current_step_index: int) -> None:
        self._conn.execute(
            "UPDATE requests SET state = ?, current_step_index = ? WHERE reference = ?",
            (state.value, current_step_index, reference),
        )
        self._conn.commit()

    def get_request(self, reference: Reference) -> RequestRecord | None:
        row = self._conn.execute("SELECT * FROM requests WHERE reference = ?", (reference,)).fetchone()
        if row is None:
            return None
        return _request_record_from_row(row)

    def list_requests(self) -> list[RequestRecord]:
        rows = self._conn.execute("SELECT * FROM requests ORDER BY seq ASC").fetchall()
        return [_request_record_from_row(row) for row in rows]

    # -- steps ----------------------------------------------------------------

    def put_step(self, reference: Reference, index: int, operation: OperationName, status: StepStatus) -> None:
        self._conn.execute(
            """
            INSERT INTO steps (reference, step_index, operation, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(reference, step_index) DO UPDATE SET status = excluded.status
            """,
            (reference, index, operation.value, status.value),
        )
        self._conn.commit()

    def list_steps(self, reference: Reference) -> list[StepRecord]:
        rows = self._conn.execute(
            "SELECT * FROM steps WHERE reference = ? ORDER BY step_index ASC", (reference,)
        ).fetchall()
        return [
            StepRecord(index=row["step_index"], operation=OperationName(row["operation"]), status=StepStatus(row["status"]))
            for row in rows
        ]

    # -- approvals --------------------------------------------------------------

    def create_approval(
        self, reference: Reference, step_index: int, operation: OperationName, required_role: ApproverRole
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO approvals (reference, step_index, operation, required_role)
            VALUES (?, ?, ?, ?)
            """,
            (reference, step_index, operation.value, required_role.value),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_pending_approval(self, reference: Reference) -> ApprovalRecord | None:
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE reference = ? AND resolved = 0 ORDER BY id DESC LIMIT 1",
            (reference,),
        ).fetchone()
        if row is None:
            return None
        return _approval_from_row(row)

    def list_pending_approvals(self) -> list[ApprovalRecord]:
        rows = self._conn.execute("SELECT * FROM approvals WHERE resolved = 0 ORDER BY id ASC").fetchall()
        return [_approval_from_row(row) for row in rows]

    def list_approvals(self, reference: Reference) -> list[ApprovalRecord]:
        rows = self._conn.execute(
            "SELECT * FROM approvals WHERE reference = ? ORDER BY id ASC", (reference,)
        ).fetchall()
        return [_approval_from_row(row) for row in rows]

    def resolve_approval(self, approval_id: int, decision: Decision, resolved_by_role: ApproverRole) -> None:
        self._conn.execute(
            "UPDATE approvals SET resolved = 1, decision = ?, resolved_by_role = ? WHERE id = ?",
            (decision.value, resolved_by_role.value, approval_id),
        )
        self._conn.commit()

    # -- log -------------------------------------------------------------------

    def append_log(self, reference: Reference, entry_type: LogEntryType, data: dict[str, str]) -> None:
        self._conn.execute(
            "INSERT INTO log (reference, entry_type, data) VALUES (?, ?, ?)",
            (reference, entry_type.value, json.dumps(data, sort_keys=True)),
        )
        self._conn.commit()

    def list_log(self, reference: Reference) -> list[LogEntry]:
        rows = self._conn.execute(
            "SELECT * FROM log WHERE reference = ? ORDER BY id ASC", (reference,)
        ).fetchall()
        return [_log_entry_from_row(row) for row in rows]

    def list_all_log(self) -> list[LogEntry]:
        rows = self._conn.execute("SELECT * FROM log ORDER BY id ASC").fetchall()
        return [_log_entry_from_row(row) for row in rows]


def _account_from_row(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        owner=row["owner"],
        tier=Tier(row["tier"]),
        balance=Money.parse(row["balance"]),
        frozen=bool(row["frozen"]),
    )


def _request_record_from_row(row: sqlite3.Row) -> RequestRecord:
    request = ServiceRequest(
        reference=row["reference"],
        kind=RequestKind(row["kind"]),
        account_id=row["account_id"],
        amount=Money.parse(row["amount"]),
        requester=Requester(
            name=row["requester_name"],
            role=row["requester_role"],
            origin=Origin(row["requester_origin"]),
        ),
    )
    return RequestRecord(
        request=request,
        state=RequestState(row["state"]),
        current_step_index=row["current_step_index"],
        seq=row["seq"],
    )


def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
    decision = Decision(row["decision"]) if row["decision"] is not None else None
    resolved_by_role = ApproverRole(row["resolved_by_role"]) if row["resolved_by_role"] is not None else None
    return ApprovalRecord(
        id=row["id"],
        reference=row["reference"],
        step_index=row["step_index"],
        operation=OperationName(row["operation"]),
        required_role=ApproverRole(row["required_role"]),
        resolved=bool(row["resolved"]),
        decision=decision,
        resolved_by_role=resolved_by_role,
    )


def _log_entry_from_row(row: sqlite3.Row) -> LogEntry:
    return LogEntry(
        id=row["id"],
        reference=row["reference"],
        entry_type=LogEntryType(row["entry_type"]),
        data=json.loads(row["data"]),
    )

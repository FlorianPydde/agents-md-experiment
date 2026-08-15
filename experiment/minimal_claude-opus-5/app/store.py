"""The append-only log and the request records, held in SQLite in this folder."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from .domain import (
    Account,
    ApprovalState,
    Decision,
    EventKind,
    OperationName,
    Origin,
    RequestKind,
    RequestState,
    Role,
    StepState,
    Tier,
)
from .errors import AppError

DEFAULT_DB_NAME = "governed.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    reference TEXT PRIMARY KEY,
    seq       INTEGER NOT NULL,
    kind      TEXT NOT NULL,
    account   TEXT NOT NULL,
    amount    TEXT NOT NULL,
    requester_name   TEXT NOT NULL,
    requester_role   TEXT NOT NULL,
    requester_origin TEXT NOT NULL,
    state     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    reference  TEXT NOT NULL,
    position   INTEGER NOT NULL,
    operation  TEXT NOT NULL,
    state      TEXT NOT NULL,
    PRIMARY KEY (reference, position)
);
CREATE TABLE IF NOT EXISTS approvals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    reference     TEXT NOT NULL,
    position      INTEGER NOT NULL,
    operation     TEXT NOT NULL,
    required_role TEXT NOT NULL,
    state         TEXT NOT NULL,
    resolved_by   TEXT,
    decision      TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
    id      TEXT PRIMARY KEY,
    owner   TEXT NOT NULL,
    tier    TEXT NOT NULL,
    balance TEXT NOT NULL,
    frozen  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    kind      TEXT NOT NULL,
    detail    TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class StepRecord:
    position: int
    operation: OperationName
    state: StepState


@dataclass(frozen=True)
class ApprovalRecord:
    id: int
    reference: str
    position: int
    operation: OperationName
    required_role: Role
    state: ApprovalState
    resolved_by: Role | None
    decision: Decision | None


@dataclass(frozen=True)
class LogRecord:
    id: int
    reference: str
    kind: EventKind
    detail: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RequestRecord:
    reference: str
    seq: int
    kind: RequestKind
    account: str
    amount: Decimal
    requester_name: str
    requester_role: str
    requester_origin: Origin
    state: RequestState


class Store:
    """Owns the database. The log table is only ever appended to."""

    def __init__(self, path: Path, *, reset: bool = False) -> None:
        self.path = path
        if reset and path.exists():
            path.unlink()
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()
        self._request_cache: dict[str, RequestView] = {}
        self.lookups = 0

    def close(self) -> None:
        self._connection.close()

    # -- writes ---------------------------------------------------------

    def append_log(
        self, reference: str, kind: EventKind, detail: tuple[tuple[str, str], ...]
    ) -> None:
        self._connection.execute(
            "INSERT INTO log (reference, kind, detail) VALUES (?, ?, ?)",
            (reference, kind.value, json.dumps([list(pair) for pair in detail])),
        )
        self._connection.commit()
        self._request_cache.pop(reference, None)

    def insert_request(self, record: RequestRecord) -> None:
        if self.find_request(record.reference) is not None:
            raise AppError(f"duplicate request reference '{record.reference}'")
        self._connection.execute(
            "INSERT INTO requests (reference, seq, kind, account, amount,"
            " requester_name, requester_role, requester_origin, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.reference,
                record.seq,
                record.kind.value,
                record.account,
                str(record.amount),
                record.requester_name,
                record.requester_role,
                record.requester_origin.value,
                record.state.value,
            ),
        )
        self._connection.commit()
        self._request_cache.pop(record.reference, None)

    def next_seq(self) -> int:
        row = self._connection.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM requests").fetchone()
        return int(row["m"]) + 1

    def set_request_state(self, reference: str, state: RequestState) -> None:
        self._connection.execute(
            "UPDATE requests SET state = ? WHERE reference = ?", (state.value, reference)
        )
        self._connection.commit()
        self._request_cache.pop(reference, None)

    def insert_steps(self, reference: str, operations: tuple[OperationName, ...]) -> None:
        self._connection.executemany(
            "INSERT INTO steps (reference, position, operation, state) VALUES (?, ?, ?, ?)",
            [
                (reference, position, operation.value, StepState.PENDING.value)
                for position, operation in enumerate(operations)
            ],
        )
        self._connection.commit()
        self._request_cache.pop(reference, None)

    def set_step_state(self, reference: str, position: int, state: StepState) -> None:
        self._connection.execute(
            "UPDATE steps SET state = ? WHERE reference = ? AND position = ?",
            (state.value, reference, position),
        )
        self._connection.commit()
        self._request_cache.pop(reference, None)

    def insert_approval(
        self, reference: str, position: int, operation: OperationName, role: Role
    ) -> None:
        self._connection.execute(
            "INSERT INTO approvals (reference, position, operation, required_role, state)"
            " VALUES (?, ?, ?, ?, ?)",
            (reference, position, operation.value, role.value, ApprovalState.PENDING.value),
        )
        self._connection.commit()
        self._request_cache.pop(reference, None)

    def resolve_approval(
        self, approval_id: int, role: Role, decision: Decision, state: ApprovalState
    ) -> None:
        self._connection.execute(
            "UPDATE approvals SET state = ?, resolved_by = ?, decision = ? WHERE id = ?",
            (state.value, role.value, decision.value, approval_id),
        )
        self._connection.commit()
        self._request_cache.clear()

    def save_accounts(self, accounts: tuple[Account, ...]) -> None:
        self._connection.executemany(
            "INSERT INTO accounts (id, owner, tier, balance, frozen) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET owner = excluded.owner, tier = excluded.tier,"
            " balance = excluded.balance, frozen = excluded.frozen",
            [
                (a.id, a.owner, a.tier.value, str(a.balance), 1 if a.frozen else 0)
                for a in accounts
            ],
        )
        self._connection.commit()

    def load_accounts(self) -> tuple[Account, ...]:
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

    # -- reads ----------------------------------------------------------

    def find_request(self, reference: str) -> RequestRecord | None:
        row = self._connection.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()
        return None if row is None else _request_from_row(row)

    def all_requests(self) -> tuple[RequestRecord, ...]:
        rows = self._connection.execute("SELECT * FROM requests ORDER BY seq").fetchall()
        return tuple(_request_from_row(row) for row in rows)

    def steps_of(self, reference: str) -> tuple[StepRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM steps WHERE reference = ? ORDER BY position", (reference,)
        ).fetchall()
        return tuple(
            StepRecord(
                position=int(row["position"]),
                operation=OperationName(row["operation"]),
                state=StepState(row["state"]),
            )
            for row in rows
        )

    def approvals_of(self, reference: str) -> tuple[ApprovalRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM approvals WHERE reference = ? ORDER BY id", (reference,)
        ).fetchall()
        return tuple(_approval_from_row(row) for row in rows)

    def pending_approval(self, reference: str) -> ApprovalRecord | None:
        row = self._connection.execute(
            "SELECT * FROM approvals WHERE reference = ? AND state = ? ORDER BY id LIMIT 1",
            (reference, ApprovalState.PENDING.value),
        ).fetchone()
        return None if row is None else _approval_from_row(row)

    def pending_approvals(self) -> tuple[ApprovalRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM approvals WHERE state = ? ORDER BY id", (ApprovalState.PENDING.value,)
        ).fetchall()
        return tuple(_approval_from_row(row) for row in rows)

    def log_of(self, reference: str) -> tuple[LogRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM log WHERE reference = ? ORDER BY id", (reference,)
        ).fetchall()
        return tuple(
            LogRecord(
                id=int(row["id"]),
                reference=row["reference"],
                kind=EventKind(row["kind"]),
                detail=tuple((str(k), str(v)) for k, v in json.loads(row["detail"])),
            )
            for row in rows
        )

    # -- memoised composite read ---------------------------------------

    def view_request(self, reference: str) -> RequestView:
        """Assemble the whole picture of one request, memoised per reference.

        `show` may be given the same reference twice; the second ask is served
        from this cache instead of touching the database again.
        """
        cached = self._request_cache.get(reference)
        if cached is not None:
            return cached
        self.lookups += 1
        record = self.find_request(reference)
        if record is None:
            raise AppError(f"no such request: {reference}")
        view = RequestView(
            request=record,
            steps=self.steps_of(reference),
            approvals=self.approvals_of(reference),
            log=self.log_of(reference),
        )
        self._request_cache[reference] = view
        return view


@dataclass(frozen=True)
class RequestView:
    request: RequestRecord
    steps: tuple[StepRecord, ...]
    approvals: tuple[ApprovalRecord, ...]
    log: tuple[LogRecord, ...]


def _request_from_row(row: sqlite3.Row) -> RequestRecord:
    return RequestRecord(
        reference=row["reference"],
        seq=int(row["seq"]),
        kind=RequestKind(row["kind"]),
        account=row["account"],
        amount=Decimal(row["amount"]),
        requester_name=row["requester_name"],
        requester_role=row["requester_role"],
        requester_origin=Origin(row["requester_origin"]),
        state=RequestState(row["state"]),
    )


def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        id=int(row["id"]),
        reference=row["reference"],
        position=int(row["position"]),
        operation=OperationName(row["operation"]),
        required_role=Role(row["required_role"]),
        state=ApprovalState(row["state"]),
        resolved_by=None if row["resolved_by"] is None else Role(row["resolved_by"]),
        decision=None if row["decision"] is None else Decision(row["decision"]),
    )


@lru_cache(maxsize=1)
def default_db_path() -> Path:
    return Path(__file__).resolve().parent.parent / DEFAULT_DB_NAME

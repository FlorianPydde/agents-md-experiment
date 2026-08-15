"""SQLite persistence: the append only log plus the state it explains."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .domain import (
    Account,
    Approval,
    RequestRecord,
    Requester,
    ServiceRequest,
    Step,
    World,
)
from .enums import (
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
from .values import Money

DEFAULT_DB = Path(__file__).resolve().parent.parent / "runner.sqlite3"

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
    idx INTEGER NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (reference, idx)
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    idx INTEGER NOT NULL,
    operation TEXT NOT NULL,
    required_role TEXT NOT NULL,
    resolution TEXT
);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    kind TEXT NOT NULL,
    details TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class LogEntry:
    id: int
    reference: str
    kind: EventKind
    details: tuple[tuple[str, str], ...]

    def presentation(self) -> str:
        body = ", ".join(f"{k}={v}" for k, v in self.details)
        return f"{self.kind}: {body}" if body else str(self.kind)


class Store:
    """Owns the database. Reads return domain objects, never rows."""

    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self._path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._cache: dict[str, RequestRecord] = {}
        self.reads = 0
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def reset(self) -> None:
        for table in ("accounts", "requests", "steps", "approvals", "log"):
            self._db.execute(f"DELETE FROM {table}")
        self._db.commit()
        self._cache.clear()

    # --- accounts -----------------------------------------------------

    def save_account(self, account: Account) -> None:
        self._db.execute(
            "INSERT INTO accounts (id, owner, tier, balance, frozen) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "owner=excluded.owner, tier=excluded.tier, "
            "balance=excluded.balance, frozen=excluded.frozen",
            (
                account.id,
                account.owner,
                account.tier.value,
                account.balance.formatted(),
                int(account.frozen),
            ),
        )
        self._db.commit()

    def save_world(self, world: World) -> None:
        for account in world.sorted_accounts():
            self.save_account(account)

    def load_world(self) -> World:
        rows = self._db.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return World(tuple(_account_of(r) for r in rows))

    # --- requests -----------------------------------------------------

    def insert_request(self, record: RequestRecord) -> None:
        request = record.request
        self._db.execute(
            "INSERT INTO requests (reference, arrival, kind, account, amount, "
            "requester_name, requester_role, requester_origin, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.reference,
                record.arrival,
                request.kind.value,
                request.account_id,
                request.amount.formatted(),
                request.requester.name,
                request.requester.role,
                request.requester.origin.value,
                record.state.value,
            ),
        )
        self._db.executemany(
            "INSERT INTO steps (reference, idx, operation, state) VALUES (?, ?, ?, ?)",
            [
                (request.reference, s.index, s.operation.value, s.state.value)
                for s in record.steps
            ],
        )
        self._db.commit()
        self._cache.clear()

    def set_request_state(self, reference: str, state: RequestState) -> None:
        self._db.execute(
            "UPDATE requests SET state = ? WHERE reference = ?",
            (state.value, reference),
        )
        self._db.commit()
        self._cache.clear()

    def set_step_state(self, reference: str, index: int, state: StepState) -> None:
        self._db.execute(
            "UPDATE steps SET state = ? WHERE reference = ? AND idx = ?",
            (state.value, reference, index),
        )
        self._db.commit()
        self._cache.clear()

    def add_approval(self, approval: Approval) -> None:
        self._db.execute(
            "INSERT INTO approvals (reference, idx, operation, required_role, "
            "resolution) VALUES (?, ?, ?, ?, ?)",
            (
                approval.reference,
                approval.step_index,
                approval.operation.value,
                approval.required_role.value,
                None,
            ),
        )
        self._db.commit()
        self._cache.clear()

    def resolve_approval(self, approval: Approval, decision: Decision) -> None:
        self._db.execute(
            "UPDATE approvals SET resolution = ? WHERE reference = ? AND idx = ? "
            "AND resolution IS NULL",
            (decision.value, approval.reference, approval.step_index),
        )
        self._db.commit()
        self._cache.clear()

    def load_request(self, reference: str) -> RequestRecord:
        """Asking twice for the same reference does the lookup once."""
        cached = self._cache.get(reference)
        if cached is not None:
            return cached
        self.reads += 1
        record = self._read_request(reference)
        self._cache[reference] = record
        return record

    def _read_request(self, reference: str) -> RequestRecord:
        row = self._db.execute(
            "SELECT * FROM requests WHERE reference = ?", (reference,)
        ).fetchone()
        if row is None:
            raise AppError(f"no such request: {reference}")
        steps = self._db.execute(
            "SELECT * FROM steps WHERE reference = ? ORDER BY idx", (reference,)
        ).fetchall()
        approvals = self._db.execute(
            "SELECT * FROM approvals WHERE reference = ? ORDER BY id", (reference,)
        ).fetchall()
        return RequestRecord(
            request=ServiceRequest(
                reference=row["reference"],
                kind=RequestKind(row["kind"]),
                account_id=row["account"],
                amount=Money(Decimal(row["amount"])),
                requester=Requester(
                    name=row["requester_name"],
                    role=row["requester_role"],
                    origin=Origin(row["requester_origin"]),
                ),
            ),
            state=RequestState(row["state"]),
            arrival=row["arrival"],
            steps=tuple(
                Step(s["idx"], OperationName(s["operation"]), StepState(s["state"]))
                for s in steps
            ),
            approvals=tuple(_approval_of(a) for a in approvals),
        )

    def all_requests(self) -> tuple[RequestRecord, ...]:
        refs = self._db.execute(
            "SELECT reference FROM requests ORDER BY arrival"
        ).fetchall()
        return tuple(self.load_request(r["reference"]) for r in refs)

    def next_arrival(self) -> int:
        row = self._db.execute("SELECT COUNT(*) AS n FROM requests").fetchone()
        return int(row["n"])

    def pending_approvals(self) -> tuple[Approval, ...]:
        rows = self._db.execute(
            "SELECT * FROM approvals WHERE resolution IS NULL ORDER BY id"
        ).fetchall()
        return tuple(_approval_of(r) for r in rows)

    # --- log ----------------------------------------------------------

    def append(
        self, reference: str, kind: EventKind, details: tuple[tuple[str, str], ...]
    ) -> None:
        self._db.execute(
            "INSERT INTO log (reference, kind, details) VALUES (?, ?, ?)",
            (reference, kind.value, json.dumps([list(d) for d in details])),
        )
        self._db.commit()

    def log_for(self, reference: str) -> tuple[LogEntry, ...]:
        rows = self._db.execute(
            "SELECT * FROM log WHERE reference = ? ORDER BY id", (reference,)
        ).fetchall()
        return tuple(
            LogEntry(
                id=r["id"],
                reference=r["reference"],
                kind=EventKind(r["kind"]),
                details=tuple((k, v) for k, v in json.loads(r["details"])),
            )
            for r in rows
        )


def _account_of(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        owner=row["owner"],
        tier=Tier(row["tier"]),
        balance=Money(Decimal(row["balance"])),
        frozen=bool(row["frozen"]),
    )


def _approval_of(row: sqlite3.Row) -> Approval:
    resolution = row["resolution"]
    return Approval(
        reference=row["reference"],
        step_index=row["idx"],
        operation=OperationName(row["operation"]),
        required_role=Role(row["required_role"]),
        resolution=None if resolution is None else Decision(resolution),
    )

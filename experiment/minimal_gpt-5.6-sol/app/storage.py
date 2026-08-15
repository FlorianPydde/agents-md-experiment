from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from .models import (
    Account,
    AppError,
    Approval,
    ApprovalState,
    Decision,
    LogEntry,
    OperationName,
    Origin,
    RequestKind,
    RequestState,
    Requester,
    Role,
    ServiceRequest,
    Step,
    StepState,
    StoredRequest,
    Tier,
    World,
)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    tier TEXT NOT NULL,
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
    arrival_order INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_reference TEXT NOT NULL REFERENCES requests(reference),
    position INTEGER NOT NULL,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    UNIQUE(request_reference, position)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_reference TEXT NOT NULL REFERENCES requests(reference),
    step_id INTEGER NOT NULL REFERENCES steps(id),
    required_role TEXT NOT NULL,
    state TEXT NOT NULL,
    decided_by TEXT,
    decision TEXT,
    UNIQUE(step_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_reference TEXT NOT NULL REFERENCES requests(reference),
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_log (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    request_reference TEXT NOT NULL,
    event TEXT NOT NULL,
    data_json TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS event_log_no_update
BEFORE UPDATE ON event_log
BEGIN
    SELECT RAISE(ABORT, 'event log is append only');
END;

CREATE TRIGGER IF NOT EXISTS event_log_no_delete
BEFORE DELETE ON event_log
BEGIN
    SELECT RAISE(ABORT, 'event log is append only');
END;
"""


class Repository:
    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def reset(self, world: World) -> None:
        self.connection.close()
        if self.path.exists():
            self.path.unlink()
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self.connection.executemany(
            "INSERT INTO accounts(id, owner, tier, balance, frozen) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    account.id,
                    account.owner,
                    account.tier.value,
                    money(account.balance),
                    int(account.frozen),
                )
                for account in world.accounts
            ],
        )
        self.connection.commit()

    def add_request(
        self, request: ServiceRequest, operations: tuple[OperationName, ...]
    ) -> None:
        if self.get_account(request.account_id) is None:
            raise AppError(f"account not found: {request.account_id}")
        try:
            with self.connection:
                next_order = self.connection.execute(
                    "SELECT COALESCE(MAX(arrival_order), 0) + 1 FROM requests"
                ).fetchone()[0]
                self.connection.execute(
                    """
                    INSERT INTO requests(
                        reference, kind, account_id, amount, requester_name,
                        requester_role, requester_origin, state, arrival_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.reference,
                        request.kind.value,
                        request.account_id,
                        money(request.amount),
                        request.requester.name,
                        request.requester.role,
                        request.requester.origin.value,
                        RequestState.RECEIVED.value,
                        next_order,
                    ),
                )
                self.connection.executemany(
                    """
                    INSERT INTO steps(request_reference, position, operation, state)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            request.reference,
                            position,
                            operation.value,
                            StepState.PENDING.value,
                        )
                        for position, operation in enumerate(operations, start=1)
                    ],
                )
        except sqlite3.IntegrityError as error:
            raise AppError(f"request already exists: {request.reference}") from error

    def get_request(self, reference: str) -> StoredRequest | None:
        row = self.connection.execute(
            """
            SELECT reference, kind, account_id, amount, requester_name,
                   requester_role, requester_origin, state, arrival_order
            FROM requests WHERE reference = ?
            """,
            (reference,),
        ).fetchone()
        return request_from_row(row) if row is not None else None

    def list_requests(self) -> tuple[StoredRequest, ...]:
        rows = self.connection.execute(
            """
            SELECT reference, kind, account_id, amount, requester_name,
                   requester_role, requester_origin, state, arrival_order
            FROM requests ORDER BY arrival_order
            """
        ).fetchall()
        return tuple(request_from_row(row) for row in rows)

    def set_request_state(self, reference: str, state: RequestState) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE requests SET state = ? WHERE reference = ?",
                (state.value, reference),
            )

    def list_steps(self, reference: str) -> tuple[Step, ...]:
        rows = self.connection.execute(
            """
            SELECT id, request_reference, position, operation, state
            FROM steps WHERE request_reference = ? ORDER BY position
            """,
            (reference,),
        ).fetchall()
        return tuple(
            Step(
                id=row[0],
                request_reference=row[1],
                position=row[2],
                operation=OperationName(row[3]),
                state=StepState(row[4]),
            )
            for row in rows
        )

    def next_step(self, reference: str) -> Step | None:
        row = self.connection.execute(
            """
            SELECT id, request_reference, position, operation, state
            FROM steps
            WHERE request_reference = ? AND state = ?
            ORDER BY position LIMIT 1
            """,
            (reference, StepState.PENDING.value),
        ).fetchone()
        if row is None:
            return None
        return Step(
            id=row[0],
            request_reference=row[1],
            position=row[2],
            operation=OperationName(row[3]),
            state=StepState(row[4]),
        )

    def set_step_state(self, step_id: int, state: StepState) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE steps SET state = ? WHERE id = ?", (state.value, step_id)
            )

    def add_approval(self, reference: str, step_id: int, role: Role) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO approvals(
                    request_reference, step_id, required_role, state
                ) VALUES (?, ?, ?, ?)
                """,
                (reference, step_id, role.value, ApprovalState.PENDING.value),
            )

    def pending_approval(self, reference: str) -> Approval | None:
        row = self.connection.execute(
            """
            SELECT id, request_reference, step_id, required_role, state,
                   decided_by, decision
            FROM approvals
            WHERE request_reference = ? AND state = ?
            """,
            (reference, ApprovalState.PENDING.value),
        ).fetchone()
        return approval_from_row(row) if row is not None else None

    def list_pending_approvals(self) -> tuple[Approval, ...]:
        rows = self.connection.execute(
            """
            SELECT id, request_reference, step_id, required_role, state,
                   decided_by, decision
            FROM approvals WHERE state = ? ORDER BY id
            """,
            (ApprovalState.PENDING.value,),
        ).fetchall()
        return tuple(approval_from_row(row) for row in rows)

    def list_approvals(self, reference: str) -> tuple[Approval, ...]:
        rows = self.connection.execute(
            """
            SELECT id, request_reference, step_id, required_role, state,
                   decided_by, decision
            FROM approvals WHERE request_reference = ? ORDER BY id
            """,
            (reference,),
        ).fetchall()
        return tuple(approval_from_row(row) for row in rows)

    def resolve_approval(
        self, approval_id: int, role: Role, decision: Decision
    ) -> None:
        state = (
            ApprovalState.APPROVED
            if decision is Decision.APPROVE
            else ApprovalState.REJECTED
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE approvals
                SET state = ?, decided_by = ?, decision = ?
                WHERE id = ?
                """,
                (state.value, role.value, decision.value, approval_id),
            )

    def get_account(self, account_id: str) -> Account | None:
        row = self.connection.execute(
            "SELECT id, owner, tier, balance, frozen FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        return account_from_row(row) if row is not None else None

    def list_accounts(self) -> tuple[Account, ...]:
        rows = self.connection.execute(
            "SELECT id, owner, tier, balance, frozen FROM accounts ORDER BY id"
        ).fetchall()
        return tuple(account_from_row(row) for row in rows)

    def set_balance(self, account_id: str, balance: Decimal) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE accounts SET balance = ? WHERE id = ?",
                (money(balance), account_id),
            )

    def set_frozen(self, account_id: str, frozen: bool) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE accounts SET frozen = ? WHERE id = ?",
                (int(frozen), account_id),
            )

    def add_notification(self, reference: str, message: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO notifications(request_reference, message) VALUES (?, ?)",
                (reference, message),
            )

    def append_event(
        self, reference: str, event: str, data: dict[str, object]
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO event_log(request_reference, event, data_json)
                VALUES (?, ?, ?)
                """,
                (reference, event, json.dumps(data, separators=(",", ":"))),
            )

    def list_events(self, reference: str) -> tuple[LogEntry, ...]:
        rows = self.connection.execute(
            """
            SELECT sequence, request_reference, event, data_json
            FROM event_log WHERE request_reference = ? ORDER BY sequence
            """,
            (reference,),
        ).fetchall()
        return tuple(
            LogEntry(
                sequence=row[0],
                request_reference=row[1],
                event=row[2],
                data=json.loads(row[3]),
            )
            for row in rows
        )


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def account_from_row(row: tuple[object, ...]) -> Account:
    return Account(
        id=str(row[0]),
        owner=str(row[1]),
        tier=Tier(str(row[2])),
        balance=Decimal(str(row[3])),
        frozen=bool(row[4]),
    )


def request_from_row(row: tuple[object, ...]) -> StoredRequest:
    return StoredRequest(
        request=ServiceRequest(
            reference=str(row[0]),
            kind=RequestKind(str(row[1])),
            account_id=str(row[2]),
            amount=Decimal(str(row[3])),
            requester=Requester(
                name=str(row[4]),
                role=str(row[5]),
                origin=Origin(str(row[6])),
            ),
        ),
        state=RequestState(str(row[7])),
        arrival_order=int(row[8]),
    )


def approval_from_row(row: tuple[object, ...]) -> Approval:
    return Approval(
        id=int(row[0]),
        request_reference=str(row[1]),
        step_id=int(row[2]),
        required_role=Role(str(row[3])),
        state=ApprovalState(str(row[4])),
        decided_by=Role(str(row[5])) if row[5] is not None else None,
        decision=Decision(str(row[6])) if row[6] is not None else None,
    )

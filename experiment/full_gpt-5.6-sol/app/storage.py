from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from .domain import (
    Account,
    AccountTier,
    ApprovalRole,
    ApprovalState,
    EventKind,
    Materiality,
    Money,
    OperationName,
    Origin,
    RequestKind,
    RequestState,
    Requester,
    ServiceRequest,
    StepState,
)


@dataclass(frozen=True)
class StepRecord:
    step_id: int
    position: int
    operation: OperationName
    state: StepState


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: int
    step_id: int
    operation: OperationName
    required_role: ApprovalRole
    state: ApprovalState
    decided_by: ApprovalRole | None


@dataclass(frozen=True)
class RequestRecord:
    request: ServiceRequest
    state: RequestState
    arrival_order: int


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, clean: bool = False) -> None:
        if clean and self.path.exists():
            self.path.unlink()
        with self.connect() as connection:
            connection.executescript(
                """
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
                CREATE TABLE IF NOT EXISTS request_steps (
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
                    step_id INTEGER NOT NULL REFERENCES request_steps(id),
                    required_role TEXT NOT NULL,
                    state TEXT NOT NULL,
                    decided_by TEXT,
                    UNIQUE(step_id)
                );
                CREATE TABLE IF NOT EXISTS event_log (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_reference TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS event_log_no_update
                BEFORE UPDATE ON event_log
                BEGIN
                    SELECT RAISE(ABORT, 'event_log is append only');
                END;
                CREATE TRIGGER IF NOT EXISTS event_log_no_delete
                BEFORE DELETE ON event_log
                BEGIN
                    SELECT RAISE(ABORT, 'event_log is append only');
                END;
                """
            )

    def seed_accounts(self, accounts: Iterable[Account]) -> None:
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO accounts(id, owner, tier, balance, frozen)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        account.account_id,
                        account.owner,
                        account.tier.value,
                        str(account.balance.amount),
                        account.frozen,
                    )
                    for account in accounts
                ],
            )

    def get_account(self, account_id: str) -> Account | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, owner, tier, balance, frozen FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        return Account(
            account_id=row["id"],
            owner=row["owner"],
            tier=AccountTier(row["tier"]),
            balance=Money(Decimal(row["balance"])),
            frozen=bool(row["frozen"]),
        )

    def save_account(self, account: Account) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE accounts SET balance = ?, frozen = ? WHERE id = ?",
                (
                    str(account.balance.amount),
                    account.frozen,
                    account.account_id,
                ),
            )

    def list_accounts(self) -> list[Account]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, owner, tier, balance, frozen FROM accounts ORDER BY id"
            ).fetchall()
        return [
            Account(
                account_id=row["id"],
                owner=row["owner"],
                tier=AccountTier(row["tier"]),
                balance=Money(Decimal(row["balance"])),
                frozen=bool(row["frozen"]),
            )
            for row in rows
        ]

    def insert_request(
        self, request: ServiceRequest, plan: tuple[OperationName, ...]
    ) -> None:
        with self.connect() as connection:
            arrival_order = connection.execute(
                "SELECT COUNT(*) FROM requests"
            ).fetchone()[0]
            connection.execute(
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
                    str(request.amount.amount),
                    request.requester.name,
                    request.requester.role,
                    request.requester.origin.value,
                    RequestState.RECEIVED.value,
                    arrival_order,
                ),
            )
            connection.executemany(
                """
                INSERT INTO request_steps(
                    request_reference, position, operation, state
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        request.reference,
                        position,
                        operation.value,
                        StepState.PENDING.value,
                    )
                    for position, operation in enumerate(plan)
                ],
            )

    def get_request(self, reference: str) -> RequestRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT reference, kind, account_id, amount, requester_name,
                       requester_role, requester_origin, state, arrival_order
                FROM requests WHERE reference = ?
                """,
                (reference,),
            ).fetchone()
        if row is None:
            return None
        return RequestRecord(
            request=ServiceRequest(
                reference=row["reference"],
                kind=RequestKind(row["kind"]),
                account_id=row["account_id"],
                amount=Money(Decimal(row["amount"])),
                requester=Requester(
                    name=row["requester_name"],
                    role=row["requester_role"],
                    origin=Origin(row["requester_origin"]),
                ),
            ),
            state=RequestState(row["state"]),
            arrival_order=row["arrival_order"],
        )

    def list_requests(self) -> list[RequestRecord]:
        with self.connect() as connection:
            references = [
                row["reference"]
                for row in connection.execute(
                    "SELECT reference FROM requests ORDER BY arrival_order"
                ).fetchall()
            ]
        return [
            record
            for reference in references
            if (record := self.get_request(reference)) is not None
        ]

    def set_request_state(self, reference: str, state: RequestState) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE requests SET state = ? WHERE reference = ?",
                (state.value, reference),
            )

    def get_steps(self, reference: str) -> list[StepRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, position, operation, state
                FROM request_steps
                WHERE request_reference = ?
                ORDER BY position
                """,
                (reference,),
            ).fetchall()
        return [
            StepRecord(
                step_id=row["id"],
                position=row["position"],
                operation=OperationName(row["operation"]),
                state=StepState(row["state"]),
            )
            for row in rows
        ]

    def next_pending_step(self, reference: str) -> StepRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, position, operation, state
                FROM request_steps
                WHERE request_reference = ? AND state = ?
                ORDER BY position LIMIT 1
                """,
                (reference, StepState.PENDING.value),
            ).fetchone()
        if row is None:
            return None
        return StepRecord(
            step_id=row["id"],
            position=row["position"],
            operation=OperationName(row["operation"]),
            state=StepState(row["state"]),
        )

    def set_step_state(self, step_id: int, state: StepState) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE request_steps SET state = ? WHERE id = ?",
                (state.value, step_id),
            )

    def skip_remaining_steps(self, reference: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE request_steps SET state = ?
                WHERE request_reference = ? AND state = ?
                """,
                (StepState.SKIPPED.value, reference, StepState.PENDING.value),
            )

    def create_approval(
        self, reference: str, step: StepRecord, role: ApprovalRole
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO approvals(
                    request_reference, step_id, required_role, state, decided_by
                ) VALUES (?, ?, ?, ?, NULL)
                """,
                (
                    reference,
                    step.step_id,
                    role.value,
                    ApprovalState.PENDING.value,
                ),
            )

    def get_pending_approval(self, reference: str) -> ApprovalRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT a.id, a.step_id, s.operation, a.required_role,
                       a.state, a.decided_by
                FROM approvals a
                JOIN request_steps s ON s.id = a.step_id
                WHERE a.request_reference = ? AND a.state = ?
                """,
                (reference, ApprovalState.PENDING.value),
            ).fetchone()
        return self._approval_from_row(row)

    def list_approvals(
        self, reference: str | None = None, pending_only: bool = False
    ) -> list[ApprovalRecord]:
        clauses: list[str] = []
        parameters: list[str] = []
        if reference is not None:
            clauses.append("a.request_reference = ?")
            parameters.append(reference)
        if pending_only:
            clauses.append("a.state = ?")
            parameters.append(ApprovalState.PENDING.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT a.id, a.step_id, s.operation, a.required_role,
                       a.state, a.decided_by
                FROM approvals a
                JOIN request_steps s ON s.id = a.step_id
                {where}
                ORDER BY a.id
                """,
                parameters,
            ).fetchall()
        return [
            approval
            for row in rows
            if (approval := self._approval_from_row(row)) is not None
        ]

    @staticmethod
    def _approval_from_row(row: sqlite3.Row | None) -> ApprovalRecord | None:
        if row is None:
            return None
        decided_by = row["decided_by"]
        return ApprovalRecord(
            approval_id=row["id"],
            step_id=row["step_id"],
            operation=OperationName(row["operation"]),
            required_role=ApprovalRole(row["required_role"]),
            state=ApprovalState(row["state"]),
            decided_by=ApprovalRole(decided_by) if decided_by else None,
        )

    def resolve_approval(
        self,
        approval: ApprovalRecord,
        state: ApprovalState,
        role: ApprovalRole,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE approvals SET state = ?, decided_by = ? WHERE id = ?",
                (state.value, role.value, approval.approval_id),
            )

    def append_event(
        self,
        reference: str,
        kind: EventKind,
        data: dict[str, str | int | bool],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO event_log(request_reference, event_kind, data_json)
                VALUES (?, ?, ?)
                """,
                (reference, kind.value, json.dumps(data, separators=(",", ":"))),
            )

    def get_events(self, reference: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, event_kind, data_json
                FROM event_log WHERE request_reference = ? ORDER BY sequence
                """,
                (reference,),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "kind": row["event_kind"],
                "data": json.loads(row["data_json"]),
            }
            for row in rows
        ]

    def operation_materialities(self) -> dict[OperationName, Materiality]:
        return {
            OperationName.READ_ACCOUNT: Materiality.READ,
            OperationName.APPLY_CREDIT: Materiality.WRITE,
            OperationName.APPLY_DEBIT: Materiality.WRITE,
            OperationName.FREEZE_ACCOUNT: Materiality.WRITE,
            OperationName.UNFREEZE_ACCOUNT: Materiality.WRITE,
            OperationName.NOTIFY_CUSTOMER: Materiality.WRITE,
        }

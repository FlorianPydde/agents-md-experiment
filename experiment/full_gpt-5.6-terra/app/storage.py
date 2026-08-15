from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterator

from .models import (
    Account,
    ApprovalRole,
    Money,
    Origin,
    RequestKind,
    RequestState,
    Requester,
    ServiceRequest,
    StepState,
    Tier,
)


class Store:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, tier TEXT NOT NULL,
              balance TEXT NOT NULL, frozen INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
              reference TEXT PRIMARY KEY, kind TEXT NOT NULL, account_id TEXT NOT NULL,
              amount TEXT NOT NULL, requester_name TEXT NOT NULL, requester_role TEXT NOT NULL,
              requester_origin TEXT NOT NULL, state TEXT NOT NULL, next_step INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_order (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS steps (
              reference TEXT NOT NULL, position INTEGER NOT NULL, operation TEXT NOT NULL,
              state TEXT NOT NULL, PRIMARY KEY (reference, position)
            );
            CREATE TABLE IF NOT EXISTS approvals (
              reference TEXT NOT NULL, step_position INTEGER NOT NULL, required_role TEXT NOT NULL,
              decision TEXT, PRIMARY KEY (reference, step_position)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT NOT NULL,
              event TEXT NOT NULL, data TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def add_account(self, account: Account) -> None:
        self.connection.execute(
            "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
            (account.identifier, account.owner, account.tier.value, account.balance.text(), account.frozen),
        )
        self.connection.commit()

    def get_account(self, identifier: str) -> Account:
        row = self.connection.execute("SELECT * FROM accounts WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise LookupError(f"account not found: {identifier}")
        return Account(row["id"], row["owner"], Tier(row["tier"]), Money.parse(row["balance"]), bool(row["frozen"]))

    def save_account(self, account: Account) -> None:
        self.connection.execute(
            "UPDATE accounts SET balance = ?, frozen = ? WHERE id = ?",
            (account.balance.text(), account.frozen, account.identifier),
        )
        self.connection.commit()

    def accounts(self) -> Iterator[Account]:
        for row in self.connection.execute("SELECT * FROM accounts ORDER BY id"):
            yield Account(row["id"], row["owner"], Tier(row["tier"]), Money.parse(row["balance"]), bool(row["frozen"]))

    def add_request(self, request: ServiceRequest, operations: tuple[str, ...]) -> None:
        self.connection.execute(
            """INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                request.reference, request.kind.value, request.account, request.amount.text(),
                request.requester.name, request.requester.role, request.requester.origin.value,
                RequestState.RECEIVED.value,
            ),
        )
        self.connection.execute("INSERT INTO request_order (reference) VALUES (?)", (request.reference,))
        self.connection.executemany(
            "INSERT INTO steps VALUES (?, ?, ?, ?)",
            [(request.reference, position, operation, StepState.PENDING.value) for position, operation in enumerate(operations)],
        )
        self.connection.commit()

    def get_request(self, reference: str) -> tuple[ServiceRequest, RequestState, int]:
        row = self.connection.execute("SELECT * FROM requests WHERE reference = ?", (reference,)).fetchone()
        if row is None:
            raise LookupError(f"request not found: {reference}")
        request = ServiceRequest(
            reference=row["reference"], kind=RequestKind(row["kind"]), account=row["account_id"],
            amount=Money.parse(row["amount"]),
            requester=Requester(row["requester_name"], row["requester_role"], Origin(row["requester_origin"])),
        )
        return request, RequestState(row["state"]), row["next_step"]

    def requests(self) -> Iterator[tuple[ServiceRequest, RequestState, int]]:
        for row in self.connection.execute(
            "SELECT r.* FROM requests r JOIN request_order o ON o.reference = r.reference ORDER BY o.sequence"
        ):
            yield self.get_request(row["reference"])

    def steps(self, reference: str) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM steps WHERE reference = ? ORDER BY position", (reference,)))

    def set_request_progress(self, reference: str, state: RequestState, next_step: int) -> None:
        self.connection.execute("UPDATE requests SET state = ?, next_step = ? WHERE reference = ?", (state.value, next_step, reference))
        self.connection.commit()

    def set_step_state(self, reference: str, position: int, state: StepState) -> None:
        self.connection.execute("UPDATE steps SET state = ? WHERE reference = ? AND position = ?", (state.value, reference, position))
        self.connection.commit()

    def request_approval(self, reference: str, position: int, role: ApprovalRole) -> None:
        self.connection.execute("INSERT INTO approvals VALUES (?, ?, ?, NULL)", (reference, position, role.value))
        self.connection.commit()

    def pending_approval(self, reference: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM approvals WHERE reference = ? AND decision IS NULL", (reference,)
        ).fetchone()

    def pending_approvals(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM approvals WHERE decision IS NULL ORDER BY reference, step_position"))

    def resolve_approval(self, reference: str, position: int, decision: str) -> None:
        self.connection.execute(
            "UPDATE approvals SET decision = ? WHERE reference = ? AND step_position = ?",
            (decision, reference, position),
        )
        self.connection.commit()

    def approvals(self, reference: str) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM approvals WHERE reference = ? ORDER BY step_position", (reference,)))

    def log(self, reference: str, event: str, **data: object) -> None:
        self.connection.execute(
            "INSERT INTO audit_log (reference, event, data) VALUES (?, ?, ?)",
            (reference, event, json.dumps(data, sort_keys=True, separators=(",", ":"))),
        )
        self.connection.commit()

    def logs(self, reference: str) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM audit_log WHERE reference = ? ORDER BY sequence", (reference,)))

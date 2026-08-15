from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from .domain import (
    Account,
    AccountArgs,
    Decision,
    Materiality,
    NotifyArgs,
    Operation,
    Origin,
    PendingApproval,
    Request,
    RequestKind,
    RequestState,
    Requester,
    Role,
    Step,
    StepState,
    AmountArgs,
)


class AppError(Exception):
    """A user-facing error which is safe to print without a traceback."""


PLAN_OPERATIONS: dict[RequestKind, tuple[Operation, ...]] = {
    RequestKind.GOODWILL_CREDIT: (
        Operation.READ_ACCOUNT,
        Operation.APPLY_CREDIT,
        Operation.NOTIFY_CUSTOMER,
    ),
    RequestKind.ACCOUNT_RECOVERY: (
        Operation.READ_ACCOUNT,
        Operation.UNFREEZE_ACCOUNT,
        Operation.APPLY_CREDIT,
        Operation.NOTIFY_CUSTOMER,
    ),
    RequestKind.COLLECT_DEBT: (
        Operation.READ_ACCOUNT,
        Operation.APPLY_DEBIT,
        Operation.NOTIFY_CUSTOMER,
    ),
}

MATERIALITY: dict[Operation, Materiality] = {
    Operation.READ_ACCOUNT: Materiality.READ,
    Operation.APPLY_CREDIT: Materiality.WRITE,
    Operation.APPLY_DEBIT: Materiality.WRITE,
    Operation.FREEZE_ACCOUNT: Materiality.WRITE,
    Operation.UNFREEZE_ACCOUNT: Materiality.WRITE,
    Operation.NOTIFY_CUSTOMER: Materiality.WRITE,
}

MESSAGE_TEMPLATES: dict[Origin, str] = {
    Origin.INTERNAL: "Internal service request completed.",
    Origin.EXTERNAL: "Your service request was completed.",
}


def _enum(value: object, enum_type: type[RequestKind] | type[Origin] | type[Role] | type[Decision], field: str):
    if not isinstance(value, str):
        raise AppError(f"{field} must be a string")
    try:
        return enum_type(value)
    except ValueError:
        choices = ", ".join(member.value for member in enum_type)
        raise AppError(f"{field} must be one of: {choices}") from None


def _required(record: dict[str, object], field: str) -> object:
    if field not in record:
        raise AppError(f"required field is absent: {field}")
    return record[field]


def _text(record: dict[str, object], field: str) -> str:
    value = _required(record, field)
    if not isinstance(value, str) or not value:
        raise AppError(f"{field} must be a non-empty string")
    return value


def _amount(value: object) -> Decimal:
    if not isinstance(value, str):
        raise AppError("amount must be a decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise AppError("amount must be a valid decimal") from None
    if not amount.is_finite() or amount < Decimal("0"):
        raise AppError("amount must be a non-negative decimal")
    return amount


def parse_request(value: object) -> Request:
    if not isinstance(value, dict):
        raise AppError("request must be an object")
    requester_value = _required(value, "requester")
    if not isinstance(requester_value, dict):
        raise AppError("requester must be an object")
    requester = Requester(
        name=_text(requester_value, "name"),
        role=_text(requester_value, "role"),
        origin=_enum(_required(requester_value, "origin"), Origin, "origin"),
    )
    return Request(
        reference=_text(value, "reference"),
        kind=_enum(_required(value, "kind"), RequestKind, "kind"),
        account=_text(value, "account"),
        amount=_amount(_required(value, "amount")),
        requester=requester,
    )


def load_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError:
        raise AppError(f"file not found: {path}") from None
    except json.JSONDecodeError as error:
        raise AppError(f"malformed JSON in {path}: {error.msg}") from None


class Store:
    def __init__(self, database_path: Path) -> None:
        self.connection = sqlite3.connect(database_path)
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
                origin TEXT NOT NULL, state TEXT NOT NULL, arrival_order INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                reference TEXT NOT NULL, position INTEGER NOT NULL, operation TEXT NOT NULL,
                state TEXT NOT NULL, PRIMARY KEY (reference, position),
                FOREIGN KEY(reference) REFERENCES requests(reference)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY, reference TEXT NOT NULL, step_position INTEGER NOT NULL,
                required_role TEXT NOT NULL, decision TEXT, resolved_role TEXT,
                UNIQUE(reference, step_position), FOREIGN KEY(reference) REFERENCES requests(reference)
            );
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY, reference TEXT NOT NULL, event TEXT NOT NULL,
                data TEXT NOT NULL
            );
            """
        )

    def event(self, reference: str, event: str, **data: object) -> None:
        self.connection.execute(
            "INSERT INTO event_log (reference, event, data) VALUES (?, ?, ?)",
            (reference, event, json.dumps(data, separators=(",", ":"), sort_keys=True)),
        )

    def add_account(self, account: Account) -> None:
        self.connection.execute(
            "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
            (account.id, account.owner, account.tier, str(account.balance), account.frozen),
        )

    def account(self, account_id: str) -> Account:
        row = self.connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise AppError(f"account does not exist: {account_id}")
        return Account(row["id"], row["owner"], row["tier"], Decimal(row["balance"]), bool(row["frozen"]))

    def update_account(self, account: Account) -> None:
        self.connection.execute(
            "UPDATE accounts SET balance = ?, frozen = ? WHERE id = ?",
            (str(account.balance), account.frozen, account.id),
        )

    def add_request(self, request: Request) -> None:
        order = self.connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        try:
            self.connection.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request.reference, request.kind.value, request.account, str(request.amount),
                 request.requester.name, request.requester.role, request.requester.origin.value,
                 RequestState.RECEIVED.value, order),
            )
        except sqlite3.IntegrityError:
            raise AppError(f"request already exists: {request.reference}") from None
        for position, operation in enumerate(PLAN_OPERATIONS[request.kind]):
            self.connection.execute(
                "INSERT INTO steps VALUES (?, ?, ?, ?)",
                (request.reference, position, operation.value, StepState.PENDING.value),
            )

    def request(self, reference: str) -> Request:
        row = self.connection.execute("SELECT * FROM requests WHERE reference = ?", (reference,)).fetchone()
        if row is None:
            raise AppError(f"request does not exist: {reference}")
        return Request(
            reference=row["reference"], kind=RequestKind(row["kind"]), account=row["account_id"],
            amount=Decimal(row["amount"]),
            requester=Requester(row["requester_name"], row["requester_role"], Origin(row["origin"])),
        )

    def request_state(self, reference: str) -> RequestState:
        row = self.connection.execute("SELECT state FROM requests WHERE reference = ?", (reference,)).fetchone()
        if row is None:
            raise AppError(f"request does not exist: {reference}")
        return RequestState(row["state"])

    def set_request_state(self, reference: str, state: RequestState) -> None:
        self.connection.execute("UPDATE requests SET state = ? WHERE reference = ?", (state.value, reference))

    def steps(self, reference: str) -> list[Step]:
        return [
            Step(row["position"], Operation(row["operation"]), StepState(row["state"]))
            for row in self.connection.execute("SELECT * FROM steps WHERE reference = ? ORDER BY position", (reference,))
        ]

    def set_step_state(self, reference: str, position: int, state: StepState) -> None:
        self.connection.execute(
            "UPDATE steps SET state = ? WHERE reference = ? AND position = ?",
            (state.value, reference, position),
        )

    def add_approval(self, pending: PendingApproval) -> None:
        self.connection.execute(
            "INSERT INTO approvals (reference, step_position, required_role) VALUES (?, ?, ?)",
            (pending.reference, pending.step_position, pending.required_role.value),
        )

    def pending_approval(self, reference: str) -> PendingApproval:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE reference = ? AND decision IS NULL", (reference,)
        ).fetchone()
        if row is None:
            raise AppError(f"request has nothing pending: {reference}")
        return PendingApproval(row["reference"], row["step_position"], Role(row["required_role"]))

    def resolve_approval(self, reference: str, role: Role, decision: Decision) -> PendingApproval:
        pending = self.pending_approval(reference)
        if role is not pending.required_role:
            raise AppError(f"approval for {reference} requires role: {pending.required_role.value}")
        self.connection.execute(
            "UPDATE approvals SET decision = ?, resolved_role = ? WHERE reference = ? AND decision IS NULL",
            (decision.value, role.value, reference),
        )
        return pending

    def request_rows(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM requests ORDER BY arrival_order"))

    def pending_rows(self) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT reference, step_position, required_role FROM approvals WHERE decision IS NULL ORDER BY id"
        ))

    def logs(self, reference: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT id, event, data FROM event_log WHERE reference = ? ORDER BY id", (reference,)
        ))

    def commit(self) -> None:
        self.connection.commit()


def policy(operation: Operation, amount: Decimal) -> Role | None:
    if MATERIALITY[operation] is Materiality.READ:
        return None
    if operation is Operation.APPLY_CREDIT:
        return Role.FINANCE if amount > Decimal("100.00") else None
    policy_roles: dict[Operation, Role] = {
        Operation.APPLY_DEBIT: Role.FINANCE,
        Operation.FREEZE_ACCOUNT: Role.RISK,
        Operation.UNFREEZE_ACCOUNT: Role.RISK,
    }
    return policy_roles.get(operation)


class Runner:
    def __init__(self, store: Store) -> None:
        self.store = store

    def intake(self, request: Request) -> None:
        self.store.account(request.account)
        self.store.add_request(request)
        self.store.event(request.reference, "request_received")
        self.store.event(
            request.reference,
            "plan_created",
            steps=[operation.value for operation in PLAN_OPERATIONS[request.kind]],
        )
        self._continue(request.reference)
        self.store.commit()

    def decide(self, reference: str, role: Role, decision: Decision) -> None:
        pending = self.store.resolve_approval(reference, role, decision)
        self.store.event(reference, "approval_resolved", role=role.value, decision=decision.value)
        if decision is Decision.REJECT:
            self.store.set_step_state(reference, pending.step_position, StepState.REJECTED)
            self.store.set_request_state(reference, RequestState.REJECTED)
            self.store.event(reference, "request_finalized", state=RequestState.REJECTED.value)
        else:
            self._run_step(reference, pending.step_position)
            if self.store.request_state(reference) is not RequestState.FAILED:
                self._continue(reference)
        self.store.commit()

    def _continue(self, reference: str) -> None:
        for step in self.store.steps(reference):
            if step.state is not StepState.PENDING:
                continue
            request = self.store.request(reference)
            required_role = policy(step.operation, request.amount)
            self.store.event(
                reference, "policy_decided", operation=step.operation.value,
                outcome="approval_required" if required_role else "run",
                required_role=required_role.value if required_role else None,
            )
            if required_role is not None:
                self.store.set_step_state(reference, step.position, StepState.AWAITING_APPROVAL)
                self.store.set_request_state(reference, RequestState.AWAITING_APPROVAL)
                self.store.add_approval(PendingApproval(reference, step.position, required_role))
                self.store.event(
                    reference, "approval_requested", step=step.operation.value, role=required_role.value
                )
                return
            self._run_step(reference, step.position)
            if self.store.request_state(reference) is RequestState.FAILED:
                return
        self.store.set_request_state(reference, RequestState.COMPLETED)
        self.store.event(reference, "request_finalized", state=RequestState.COMPLETED.value)

    def _run_step(self, reference: str, position: int) -> None:
        step = self.store.steps(reference)[position]
        request = self.store.request(reference)
        self.store.event(reference, "operation_running", operation=step.operation.value)
        try:
            OPERATION_HANDLERS[step.operation](self.store, request)
        except AppError as error:
            self.store.set_step_state(reference, position, StepState.FAILED)
            self.store.set_request_state(reference, RequestState.FAILED)
            self.store.event(reference, "operation_failed", operation=step.operation.value, reason=str(error))
            self.store.event(reference, "request_finalized", state=RequestState.FAILED.value)
            return
        self.store.set_step_state(reference, position, StepState.COMPLETED)


def _account_operation(store: Store, request: Request) -> None:
    account = store.account(AccountArgs(request.account).account_id)
    store.event(request.reference, "account_read", balance=f"{account.balance:.2f}", tier=account.tier, frozen=account.frozen)


def _credit_operation(store: Store, request: Request) -> None:
    args = AmountArgs(request.account, request.amount)
    account = store.account(args.account_id)
    if account.frozen:
        raise AppError("account is frozen")
    store.update_account(Account(account.id, account.owner, account.tier, account.balance + args.amount, account.frozen))


def _debit_operation(store: Store, request: Request) -> None:
    args = AmountArgs(request.account, request.amount)
    account = store.account(args.account_id)
    if account.frozen:
        raise AppError("account is frozen")
    if account.balance < args.amount:
        raise AppError("insufficient balance")
    store.update_account(Account(account.id, account.owner, account.tier, account.balance - args.amount, account.frozen))


def _unfreeze_operation(store: Store, request: Request) -> None:
    account = store.account(AccountArgs(request.account).account_id)
    store.update_account(Account(account.id, account.owner, account.tier, account.balance, False))


def _freeze_operation(store: Store, request: Request) -> None:
    account = store.account(AccountArgs(request.account).account_id)
    store.update_account(Account(account.id, account.owner, account.tier, account.balance, True))


def _notify_operation(store: Store, request: Request) -> None:
    args = NotifyArgs(request.account, request.requester.origin)
    account = store.account(args.account_id)
    if account.frozen:
        raise AppError("account is frozen")
    store.event(request.reference, "customer_notified", template=MESSAGE_TEMPLATES[args.origin])


OPERATION_HANDLERS: dict[Operation, Callable[[Store, Request], None]] = {
    Operation.READ_ACCOUNT: _account_operation,
    Operation.APPLY_CREDIT: _credit_operation,
    Operation.APPLY_DEBIT: _debit_operation,
    Operation.UNFREEZE_ACCOUNT: _unfreeze_operation,
    Operation.NOTIFY_CUSTOMER: _notify_operation,
    Operation.FREEZE_ACCOUNT: _freeze_operation,
}


def load_world(store: Store, path: Path) -> None:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise AppError("world must be an object")
    accounts = _required(payload, "accounts")
    if not isinstance(accounts, list):
        raise AppError("accounts must be a list")
    for value in accounts:
        if not isinstance(value, dict):
            raise AppError("account must be an object")
        frozen = _required(value, "frozen")
        if not isinstance(frozen, bool):
            raise AppError("frozen must be a boolean")
        store.add_account(Account(
            id=_text(value, "id"), owner=_text(value, "owner"), tier=_text(value, "tier"),
            balance=_amount(_required(value, "balance")), frozen=frozen,
        ))
    store.commit()


def replay(store: Store, scenario_path: Path) -> None:
    payload = load_json(scenario_path)
    if not isinstance(payload, dict):
        raise AppError("scenario must be an object")
    entries = _required(payload, "steps")
    if not isinstance(entries, list):
        raise AppError("steps must be a list")
    runner = Runner(store)
    for entry in entries:
        if not isinstance(entry, dict):
            raise AppError("scenario step must be an object")
        action = _text(entry, "action")
        if action == "intake":
            runner.intake(parse_request(_required(entry, "request")))
        elif action == "decide":
            runner.decide(
                _text(entry, "reference"),
                _enum(_required(entry, "role"), Role, "role"),
                _enum(_required(entry, "decision"), Decision, "decision"),
            )
        else:
            raise AppError("action must be one of: intake, decide")


def serialize_request(store: Store, reference: str) -> dict[str, object]:
    request = store.request(reference)
    return {
        "reference": request.reference, "kind": request.kind.value, "account": request.account,
        "amount": f"{request.amount:.2f}", "state": store.request_state(reference).value,
        "steps": [{"position": step.position, "operation": step.operation.value, "state": step.state.value}
                  for step in store.steps(reference)],
    }

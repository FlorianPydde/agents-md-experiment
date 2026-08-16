"""The engine: turns requests into plans and runs them under policy control."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from app.errors import AppError
from app.models import (
    ACCOUNT_TIERS,
    PLANS,
    REQUESTER_ORIGINS,
    format_amount,
    parse_amount,
)
from app.operations import OperationFailed, run_operation
from app.policy import evaluate
from app.storage import append_log

VALID_ROLES_APPROVING = ("finance", "risk", "supervisor")
VALID_DECISIONS = ("approve", "reject")


def _account_amount(operation: str) -> bool:
    return operation in ("apply_credit", "apply_debit")


def validate_world(world: dict) -> None:
    if not isinstance(world, dict) or "accounts" not in world:
        raise AppError("world file must hold an 'accounts' list")
    accounts = world["accounts"]
    if not isinstance(accounts, list):
        raise AppError("world 'accounts' must be a list")
    seen: set[str] = set()
    for account in accounts:
        for field in ("id", "owner", "tier", "balance", "frozen"):
            if field not in account:
                raise AppError(f"account is missing required field '{field}'")
        if account["id"] in seen:
            raise AppError(f"duplicate account id: {account['id']}")
        seen.add(account["id"])
        if account["tier"] not in ACCOUNT_TIERS:
            raise AppError(
                f"account {account['id']} has an unknown tier: {account['tier']!r}"
            )
        if not isinstance(account["frozen"], bool):
            raise AppError(f"account {account['id']} 'frozen' must be a boolean")
        parse_amount(account["balance"], field=f"account {account['id']} balance")


def _load_account(conn: sqlite3.Connection, account_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if row is None:
        raise AppError(f"unknown account: {account_id}")
    return {
        "id": row["id"],
        "owner": row["owner"],
        "tier": row["tier"],
        "balance": Decimal(row["balance"]),
        "frozen": bool(row["frozen"]),
    }


def _save_account(conn: sqlite3.Connection, account: dict) -> None:
    conn.execute(
        "UPDATE accounts SET owner = ?, tier = ?, balance = ?, frozen = ? WHERE id = ?",
        (
            account["owner"],
            account["tier"],
            format_amount(account["balance"]),
            1 if account["frozen"] else 0,
            account["id"],
        ),
    )


def _request_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "reference": row["reference"],
        "kind": row["kind"],
        "account": row["account"],
        "amount": Decimal(row["amount"]),
        "requester_name": row["requester_name"],
        "requester_role": row["requester_role"],
        "requester_origin": row["requester_origin"],
        "state": row["state"],
        "current_step": row["current_step"],
    }


def _load_request(conn: sqlite3.Connection, reference: str) -> dict:
    row = conn.execute(
        "SELECT * FROM requests WHERE reference = ?", (reference,)
    ).fetchone()
    if row is None:
        raise AppError(f"unknown request reference: {reference}")
    return _request_row_to_dict(row)


def validate_request_payload(request: dict) -> None:
    required = ("reference", "kind", "account", "amount", "requester")
    for field in required:
        if field not in request:
            raise AppError(f"request is missing required field '{field}'")
    if request["kind"] not in PLANS:
        raise AppError(f"unknown request kind: {request['kind']!r}")
    requester = request["requester"]
    for field in ("name", "role", "origin"):
        if field not in requester:
            raise AppError(f"requester is missing required field '{field}'")
    if requester["origin"] not in REQUESTER_ORIGINS:
        raise AppError(f"unknown requester origin: {requester['origin']!r}")
    parse_amount(request["amount"])


def intake(conn: sqlite3.Connection, request: dict) -> None:
    """Receive a new service request, plan it, and try to run it."""

    validate_request_payload(request)
    reference = request["reference"]

    existing = conn.execute(
        "SELECT 1 FROM requests WHERE reference = ?", (reference,)
    ).fetchone()
    if existing is not None:
        raise AppError(f"duplicate request reference: {reference}")

    account_row = conn.execute(
        "SELECT 1 FROM accounts WHERE id = ?", (request["account"],)
    ).fetchone()
    if account_row is None:
        raise AppError(f"request {reference} names an unknown account: {request['account']}")

    amount = parse_amount(request["amount"])
    requester = request["requester"]

    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM requests"
    ).fetchone()["n"]

    conn.execute(
        "INSERT INTO requests"
        " (reference, seq, kind, account, amount, requester_name,"
        "  requester_role, requester_origin, state, current_step)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', 0)",
        (
            reference,
            seq,
            request["kind"],
            request["account"],
            format_amount(amount),
            requester["name"],
            requester["role"],
            requester["origin"],
        ),
    )

    append_log(
        conn,
        "request_received",
        {
            "reference": reference,
            "kind": request["kind"],
            "account": request["account"],
            "amount": format_amount(amount),
            "requester": requester,
        },
        request_ref=reference,
    )

    plan = PLANS[request["kind"]]
    for idx, operation in enumerate(plan):
        conn.execute(
            "INSERT INTO steps (request_ref, idx, operation, state)"
            " VALUES (?, ?, ?, 'pending')",
            (reference, idx, operation),
        )

    append_log(
        conn,
        "plan_created",
        {"reference": reference, "steps": list(plan)},
        request_ref=reference,
    )

    _run_steps(conn, reference)


def _set_request_state(conn: sqlite3.Connection, reference: str, state: str) -> None:
    conn.execute(
        "UPDATE requests SET state = ? WHERE reference = ?", (state, reference)
    )
    if state in ("completed", "rejected", "failed"):
        append_log(conn, "request_finished", {"reference": reference, "state": state}, request_ref=reference)


def _skip_remaining_steps(conn: sqlite3.Connection, reference: str, from_idx: int) -> None:
    conn.execute(
        "UPDATE steps SET state = 'skipped'"
        " WHERE request_ref = ? AND idx >= ? AND state = 'pending'",
        (reference, from_idx),
    )


def _run_steps(conn: sqlite3.Connection, reference: str) -> None:
    """Run steps in order, stopping at an approval, a failure, or the end."""

    while True:
        request = _load_request(conn, reference)
        plan = PLANS[request["kind"]]
        idx = request["current_step"]

        if idx >= len(plan):
            _set_request_state(conn, reference, "completed")
            return

        operation = plan[idx]
        amount = request["amount"] if _account_amount(operation) else None
        decision = evaluate(operation, amount)

        if decision.needs_approval:
            conn.execute(
                "INSERT INTO approvals (request_ref, step_idx, role, status)"
                " VALUES (?, ?, ?, 'pending')",
                (reference, idx, decision.role),
            )
            append_log(
                conn,
                "approval_requested",
                {"reference": reference, "step": idx, "operation": operation, "role": decision.role},
                request_ref=reference,
            )
            _set_request_state(conn, reference, "awaiting_approval")
            return

        append_log(
            conn,
            "policy_decision",
            {"reference": reference, "step": idx, "operation": operation, "auto": True},
            request_ref=reference,
        )

        if not _execute_step(conn, reference, idx, operation):
            return

        conn.execute(
            "UPDATE requests SET current_step = ? WHERE reference = ?",
            (idx + 1, reference),
        )


def _execute_step(conn: sqlite3.Connection, reference: str, idx: int, operation: str) -> bool:
    """Run one step's operation. Returns True on success, False on failure."""

    request = _load_request(conn, reference)
    account = _load_account(conn, request["account"])

    try:
        result = run_operation(operation, account, request)
    except OperationFailed as exc:
        conn.execute(
            "UPDATE steps SET state = 'failed' WHERE request_ref = ? AND idx = ?",
            (reference, idx),
        )
        append_log(
            conn,
            "operation_failed",
            {"reference": reference, "step": idx, "operation": operation, "reason": str(exc)},
            request_ref=reference,
        )
        _skip_remaining_steps(conn, reference, idx + 1)
        _set_request_state(conn, reference, "failed")
        return False

    _save_account(conn, account)
    conn.execute(
        "UPDATE steps SET state = 'done' WHERE request_ref = ? AND idx = ?",
        (reference, idx),
    )
    append_log(
        conn,
        "operation_run",
        {"reference": reference, "step": idx, "operation": operation, "result": result},
        request_ref=reference,
    )
    return True


def decide(conn: sqlite3.Connection, reference: str, role: str, decision: str) -> None:
    """Resolve a pending approval for ``reference``."""

    if decision not in VALID_DECISIONS:
        raise AppError(f"unknown decision: {decision!r}")
    if role not in VALID_ROLES_APPROVING:
        raise AppError(f"unknown approving role: {role!r}")

    request = _load_request(conn, reference)
    if request["state"] != "awaiting_approval":
        raise AppError(f"request {reference} has nothing pending")

    approval = conn.execute(
        "SELECT * FROM approvals WHERE request_ref = ? AND status = 'pending'"
        " ORDER BY step_idx LIMIT 1",
        (reference,),
    ).fetchone()
    if approval is None:
        raise AppError(f"request {reference} has nothing pending")

    if role != approval["role"]:
        raise AppError(
            f"decision for {reference} requires role '{approval['role']}', not '{role}'"
        )

    status = "approved" if decision == "approve" else "rejected"
    conn.execute(
        "UPDATE approvals SET status = ?, decided_by_role = ?, decision = ? WHERE id = ?",
        (status, role, decision, approval["id"]),
    )
    append_log(
        conn,
        "approval_resolved",
        {
            "reference": reference,
            "step": approval["step_idx"],
            "role": role,
            "decision": decision,
        },
        request_ref=reference,
    )

    idx = approval["step_idx"]
    operation = PLANS[request["kind"]][idx]

    if decision == "reject":
        _skip_remaining_steps(conn, reference, idx)
        _set_request_state(conn, reference, "rejected")
        return

    if not _execute_step(conn, reference, idx, operation):
        return

    conn.execute(
        "UPDATE requests SET current_step = ? WHERE reference = ?",
        (idx + 1, reference),
    )
    _run_steps(conn, reference)

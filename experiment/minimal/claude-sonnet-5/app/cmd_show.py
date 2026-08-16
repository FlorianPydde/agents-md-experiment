"""`python -m app show REFERENCE [REFERENCE ...]`

Prints a request, its steps, its approvals, and its log entries. Looking up
several references only performs the underlying database lookup once per
distinct reference, even if the same reference is named more than once.
"""

from __future__ import annotations

from app.engine import Engine
from app.errors import AppError

DEFAULT_DB_PATH = "log.db"


def _format_step(step) -> str:
    return f"  [{step['step_index']}] {step['operation']}: {step['state']}"


def _format_approval(approval) -> str:
    if approval["status"] == "pending":
        return f"  step {approval['step_index']} requires {approval['role_required']}: pending"
    return (
        f"  step {approval['step_index']} requires {approval['role_required']}: "
        f"{approval['status']} (decided by {approval['decided_role']}, {approval['decision']})"
    )


def _format_log_entry(entry: dict) -> str:
    return f"  {entry['event']} {entry['data']}"


def _build_report(engine: Engine, reference: str) -> str:
    row = engine.get_request(reference)
    if row is None:
        raise AppError(f"no such request '{reference}'")

    lines = [
        f"Request {row['reference']}",
        f"  kind: {row['kind']}",
        f"  account: {row['account']}",
        f"  amount: {row['amount']}",
        f"  requester: {row['requester_name']} ({row['requester_role']}, {row['requester_origin']})",
        f"  state: {row['state']}",
        "Steps:",
    ]
    lines.extend(_format_step(step) for step in engine.get_steps(reference))

    approvals = engine.get_approvals(reference)
    lines.append("Approvals:")
    if approvals:
        lines.extend(_format_approval(a) for a in approvals)
    else:
        lines.append("  none")

    lines.append("Log:")
    lines.extend(_format_log_entry(entry) for entry in engine.get_log(reference))

    return "\n".join(lines)


def show_command(references: list[str], db_path: str = DEFAULT_DB_PATH) -> str:
    engine = Engine.open_existing(db_path)
    cache: dict[str, str] = {}
    reports = []
    for reference in references:
        if reference not in cache:
            cache[reference] = _build_report(engine, reference)
        reports.append(cache[reference])
    return "\n\n".join(reports)

"""Plan registry: which operations a request kind runs, and in what order."""

from __future__ import annotations

from app.domain import OperationName, Plan, PlannedStep, RequestKind


def _plan(*operations: OperationName) -> Plan:
    return Plan(steps=tuple(PlannedStep(index=i, operation=op) for i, op in enumerate(operations)))


PLANS: dict[RequestKind, Plan] = {
    RequestKind.GOODWILL_CREDIT: _plan(
        OperationName.READ_ACCOUNT,
        OperationName.APPLY_CREDIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
    RequestKind.ACCOUNT_RECOVERY: _plan(
        OperationName.READ_ACCOUNT,
        OperationName.UNFREEZE_ACCOUNT,
        OperationName.APPLY_CREDIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
    RequestKind.COLLECT_DEBT: _plan(
        OperationName.READ_ACCOUNT,
        OperationName.APPLY_DEBIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
}

if missing := set(RequestKind) - PLANS.keys():
    raise RuntimeError(f"No plan registered for: {missing}")


def plan_for(kind: RequestKind) -> Plan:
    return PLANS[kind]

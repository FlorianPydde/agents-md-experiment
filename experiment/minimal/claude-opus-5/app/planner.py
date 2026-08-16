"""Turning a request into an ordered plan of steps."""

from __future__ import annotations

from .domain import OperationName, RequestKind, ServiceRequest, Step, StepState
from .money import format_amount

PLANS: dict[RequestKind, tuple[OperationName, ...]] = {
    RequestKind.GOODWILL_CREDIT: (
        OperationName.READ_ACCOUNT,
        OperationName.APPLY_CREDIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
    RequestKind.ACCOUNT_RECOVERY: (
        OperationName.READ_ACCOUNT,
        OperationName.UNFREEZE_ACCOUNT,
        OperationName.APPLY_CREDIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
    RequestKind.COLLECT_DEBT: (
        OperationName.READ_ACCOUNT,
        OperationName.APPLY_DEBIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
}


def _arguments(request: ServiceRequest, operation: OperationName) -> dict[str, str]:
    arguments = {"account": request.account}
    if operation in (OperationName.APPLY_CREDIT, OperationName.APPLY_DEBIT):
        arguments["amount"] = format_amount(request.amount)
    if operation is OperationName.NOTIFY_CUSTOMER:
        arguments["reference"] = request.reference
        arguments["origin"] = request.requester.origin.value
    return arguments


def plan_for(request: ServiceRequest) -> tuple[Step, ...]:
    """The ordered steps for a request, with the arguments each step will run on."""
    return tuple(
        Step(
            index=index,
            operation=operation,
            arguments=_arguments(request, operation),
            state=StepState.PENDING,
        )
        for index, operation in enumerate(PLANS[request.kind])
    )

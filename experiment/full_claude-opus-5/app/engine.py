"""The orchestrator: intake, policy-gated step execution, and approval resolution."""

from __future__ import annotations

from app.domain import (
    ApproverRole,
    Decision,
    DomainError,
    LogEntryType,
    PlannedStep,
    Reference,
    RequestState,
    ServiceRequest,
    StepStatus,
)
from app.operations import Operation, OperationContext, operation_for
from app.plans import plan_for
from app.policy import Autonomous, NeedsApproval, PolicyContext, decide as decide_policy
from app.store import Store


class EngineError(DomainError):
    """Raised when a scenario command cannot be applied."""


def intake_request(store: Store, request: ServiceRequest, seq: int) -> None:
    if store.get_request(request.reference) is not None:
        raise EngineError(f"duplicate request reference: {request.reference}")
    account = store.get_account(request.account_id)
    if account is None:
        raise EngineError(f"request {request.reference} names unknown account: {request.account_id}")

    store.put_request(request, RequestState.RECEIVED, current_step_index=0, seq=seq)
    store.append_log(
        request.reference,
        LogEntryType.REQUEST_RECEIVED,
        {
            "kind": request.kind.value,
            "account": request.account_id,
            "amount": request.amount.formatted(),
            "requester": request.requester.name,
            "origin": request.requester.origin.value,
        },
    )

    plan = plan_for(request.kind)
    for step in plan.steps:
        store.put_step(request.reference, step.index, step.operation, StepStatus.PENDING)
    store.append_log(
        request.reference,
        LogEntryType.PLAN_CREATED,
        {"steps": ",".join(step.operation.value for step in plan.steps)},
    )

    _advance(store, request.reference)


def apply_decision(store: Store, reference: Reference, role: ApproverRole, decision: Decision) -> None:
    record = store.get_request(reference)
    if record is None:
        raise EngineError(f"decision names unknown request: {reference}")

    approval = store.get_pending_approval(reference)
    if approval is None:
        raise EngineError(f"no pending approval for request: {reference}")
    if role is not approval.required_role:
        raise EngineError(
            f"approval for {reference} requires role {approval.required_role.value}, not {role.value}"
        )

    store.resolve_approval(approval.id, decision, role)
    store.append_log(
        reference,
        LogEntryType.APPROVAL_RESOLVED,
        {"role": role.value, "decision": decision.value, "operation": approval.operation.value},
    )

    if decision is Decision.REJECT:
        store.put_step(reference, approval.step_index, approval.operation, StepStatus.REJECTED)
        _finalize(store, reference, RequestState.REJECTED)
        return

    plan = plan_for(record.request.kind)
    step = plan.step_at(approval.step_index)
    assert step is not None
    operation = operation_for(step.operation)
    if not _run_step(store, reference, record.request.account_id, operation, step):
        return

    if plan.is_last(step.index):
        _finalize(store, reference, RequestState.COMPLETED)
        return

    store.update_request(reference, RequestState.RECEIVED, step.index + 1)
    _advance(store, reference)


def _advance(store: Store, reference: Reference) -> None:
    while True:
        record = store.get_request(reference)
        if record is None or record.state is not RequestState.RECEIVED:
            return

        plan = plan_for(record.request.kind)
        step = plan.step_at(record.current_step_index)
        if step is None:
            _finalize(store, reference, RequestState.COMPLETED)
            return

        operation = operation_for(step.operation)
        ctx = PolicyContext(operation=step.operation, materiality=operation.materiality, amount=record.request.amount)
        outcome = decide_policy(ctx)
        store.append_log(
            reference,
            LogEntryType.POLICY_DECIDED,
            {
                "operation": step.operation.value,
                "outcome": "autonomous" if isinstance(outcome, Autonomous) else "needs_approval",
                "role": outcome.role.value if isinstance(outcome, NeedsApproval) else "",
            },
        )

        if isinstance(outcome, NeedsApproval):
            store.put_step(reference, step.index, step.operation, StepStatus.AWAITING_APPROVAL)
            store.create_approval(reference, step.index, step.operation, outcome.role)
            store.append_log(
                reference,
                LogEntryType.APPROVAL_REQUESTED,
                {"operation": step.operation.value, "role": outcome.role.value},
            )
            store.update_request(reference, RequestState.AWAITING_APPROVAL, step.index)
            return

        if not _run_step(store, reference, record.request.account_id, operation, step):
            return

        if plan.is_last(step.index):
            _finalize(store, reference, RequestState.COMPLETED)
            return

        store.update_request(reference, RequestState.RECEIVED, step.index + 1)


def _run_step(store: Store, reference: Reference, account_id: str, operation: Operation, step: PlannedStep) -> bool:
    record = store.get_request(reference)
    assert record is not None
    account = store.get_account(account_id)
    assert account is not None
    ctx = OperationContext(account=account, amount=record.request.amount, requester=record.request.requester)

    try:
        operation.check(ctx)
        result = operation.execute(ctx)
    except DomainError as exc:
        store.put_step(reference, step.index, step.operation, StepStatus.FAILED)
        store.append_log(
            reference,
            LogEntryType.OPERATION_FAILED,
            {"operation": step.operation.value, "reason": str(exc)},
        )
        _finalize(store, reference, RequestState.FAILED)
        return False

    store.put_account(result.account)
    store.put_step(reference, step.index, step.operation, StepStatus.DONE)
    store.append_log(
        reference,
        LogEntryType.OPERATION_RAN,
        {"operation": step.operation.value, "detail": result.detail},
    )
    return True


def _finalize(store: Store, reference: Reference, state: RequestState) -> None:
    record = store.get_request(reference)
    assert record is not None
    store.update_request(reference, state, record.current_step_index)
    store.append_log(reference, LogEntryType.REQUEST_FINALIZED, {"state": state.value})

"""The engine: replays a scenario against a world and a store.

Handles intake (planning + running steps until an approval or the end),
and decide (resolving a pending approval and continuing the run).
"""

from __future__ import annotations

from app.domain import (
    PLANS,
    ApprovalState,
    DecideEvent,
    RequestState,
    Scenario,
    ServiceRequest,
    StepState,
    World,
)
from app.errors import AppError
from app.log import LogEventKind
from app.operations import OperationArgs, OperationError, execute
from app.policy import decide as policy_decide
from app.store import Store


def intake(store: Store, world: World, request: ServiceRequest, order_index: int) -> None:
    if store.get_request(request.reference) is not None:
        raise AppError(f"duplicate request reference: {request.reference!r}")
    account = world.accounts.get(request.account)
    if account is None:
        raise AppError(f"request {request.reference!r} references unknown account {request.account!r}")

    store.insert_request(
        reference=request.reference,
        kind=request.kind,
        account=request.account,
        amount=request.amount,
        requester_name=request.requester.name,
        requester_role=request.requester.role.value,
        requester_origin=request.requester.origin.value,
        state=RequestState.RECEIVED,
        order_index=order_index,
    )
    store.append_log(
        LogEventKind.REQUEST_RECEIVED,
        request.reference,
        {
            "kind": request.kind.value,
            "account": request.account,
            "amount": str(request.amount),
            "requester": request.requester.name,
            "requester_role": request.requester.role.value,
            "requester_origin": request.requester.origin.value,
        },
    )

    plan = PLANS[request.kind]
    store.append_log(
        LogEventKind.PLAN_CREATED, request.reference, {"steps": [op.value for op in plan]}
    )
    for step_index, operation in enumerate(plan):
        store.insert_step(request.reference, step_index, operation, StepState.PENDING)

    _run_from_step(store, request.reference, request.account, request.amount, request.requester.origin, 0)


def decide(store: Store, event: DecideEvent) -> None:
    stored = store.get_request(event.reference)
    if stored is None:
        raise AppError(f"decision references unknown request: {event.reference!r}")

    pending = store.get_pending_approval(event.reference)
    if pending is None:
        raise AppError(f"no pending approval for request {event.reference!r}")
    if pending.required_role is not event.role:
        raise AppError(
            f"decision for {event.reference!r} requires role {pending.required_role.value!r}, "
            f"got {event.role.value!r}"
        )

    requester = store.get_request_requester(event.reference)
    assert requester is not None
    _, _, requester_origin = requester
    from app.domain import Origin

    origin = Origin(requester_origin)

    if event.decision.value == "approve":
        store.resolve_approval(event.reference, pending.step_index, ApprovalState.APPROVED, event.role)
        store.append_log(
            LogEventKind.APPROVAL_RESOLVED,
            event.reference,
            {"step_index": pending.step_index, "role": event.role.value, "decision": "approve"},
        )
        store.update_request_state(event.reference, RequestState.RECEIVED)
        _run_approved_step(
            store, event.reference, stored.account, stored.amount, origin, pending.step_index
        )
    else:
        store.resolve_approval(event.reference, pending.step_index, ApprovalState.REJECTED, event.role)
        store.append_log(
            LogEventKind.APPROVAL_RESOLVED,
            event.reference,
            {"step_index": pending.step_index, "role": event.role.value, "decision": "reject"},
        )
        store.update_step_state(event.reference, pending.step_index, StepState.SKIPPED)
        _set_final_state(store, event.reference, RequestState.REJECTED)


def _run_approved_step(
    store: Store, reference: str, account_id: str, amount, origin, step_index: int
) -> None:
    """Run the single step that was just approved, then continue from the next step."""
    steps = {s.step_index: s for s in store.get_steps(reference)}
    step = steps[step_index]
    account = store.get_account(account_id)
    assert account is not None
    args = OperationArgs(account=account, amount=amount, origin=origin)
    try:
        result = execute(step.operation, args)
    except OperationError as exc:
        store.append_log(
            LogEventKind.OPERATION_FAILED,
            reference,
            {"step_index": step.step_index, "operation": step.operation.value, "error": str(exc)},
        )
        store.update_step_state(reference, step.step_index, StepState.FAILED)
        _set_final_state(store, reference, RequestState.FAILED)
        return

    store.put_account(result.account)
    store.append_log(
        LogEventKind.OPERATION_RAN,
        reference,
        {"step_index": step.step_index, "operation": step.operation.value, "detail": result.detail},
    )
    store.update_step_state(reference, step.step_index, StepState.DONE)
    _run_from_step(store, reference, account_id, amount, origin, step_index + 1)


def _run_from_step(store: Store, reference: str, account_id: str, amount, origin, start_index: int) -> None:
    steps = store.get_steps(reference)
    for step in steps[start_index:]:
        account = store.get_account(account_id)
        assert account is not None
        pd = policy_decide(step.operation, amount)
        store.append_log(
            LogEventKind.POLICY_DECIDED,
            reference,
            {
                "step_index": step.step_index,
                "operation": step.operation.value,
                "needs_approval": pd.needs_approval,
                "required_role": pd.required_role.value if pd.required_role else None,
                "rule": pd.rule,
            },
        )
        if pd.needs_approval:
            store.insert_approval(reference, step.step_index, pd.required_role, ApprovalState.PENDING)
            store.append_log(
                LogEventKind.APPROVAL_REQUESTED,
                reference,
                {"step_index": step.step_index, "operation": step.operation.value, "role": pd.required_role.value},
            )
            store.update_step_state(reference, step.step_index, StepState.AWAITING_APPROVAL)
            store.update_request_state(reference, RequestState.AWAITING_APPROVAL)
            store.append_log(
                LogEventKind.REQUEST_STATE_CHANGED,
                reference,
                {"state": RequestState.AWAITING_APPROVAL.value},
            )
            return

        args = OperationArgs(account=account, amount=amount, origin=origin)
        try:
            result = execute(step.operation, args)
        except OperationError as exc:
            store.append_log(
                LogEventKind.OPERATION_FAILED,
                reference,
                {"step_index": step.step_index, "operation": step.operation.value, "error": str(exc)},
            )
            store.update_step_state(reference, step.step_index, StepState.FAILED)
            _set_final_state(store, reference, RequestState.FAILED)
            return

        store.put_account(result.account)
        store.append_log(
            LogEventKind.OPERATION_RAN,
            reference,
            {"step_index": step.step_index, "operation": step.operation.value, "detail": result.detail},
        )
        store.update_step_state(reference, step.step_index, StepState.DONE)

    _set_final_state(store, reference, RequestState.COMPLETED)


def _set_final_state(store: Store, reference: str, state: RequestState) -> None:
    store.update_request_state(reference, state)
    store.append_log(LogEventKind.REQUEST_STATE_CHANGED, reference, {"state": state.value})


def replay(store: Store, world: World, scenario: Scenario) -> None:
    for account in world.accounts.values():
        store.put_account(account)

    order_index = 0
    for event in scenario.events:
        if isinstance(event, ServiceRequest):
            intake(store, world, event, order_index)
            order_index += 1
        else:
            decide(store, event)
        store.commit()

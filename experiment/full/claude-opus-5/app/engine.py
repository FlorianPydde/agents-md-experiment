"""The runner. Steps run in order, under policy, and everything is logged."""

from typing import Any

from app.domain import Account, DecisionCommand, ServiceRequest
from app.enums import (
    ApprovalState,
    Decision,
    EventKind,
    RequestState,
    StepState,
)
from app.errors import OperationFailure, ServiceRequestError
from app.events import LogEntry
from app.operations import OPERATIONS
from app.plans import Step, plan_for
from app.policy import PolicyQuery, decide as decide_policy
from app.store import Store


def intake(store: Store, request: ServiceRequest) -> None:
    """Accept a request, plan it, and run it as far as policy allows."""
    store.add_request(request)
    store.append(
        LogEntry(
            EventKind.REQUEST_RECEIVED,
            request.reference,
            (
                ("kind", request.kind.value),
                ("account", request.account),
                ("amount", str(request.amount)),
                ("requester", request.requester.name),
                ("origin", request.requester.origin.value),
            ),
        )
    )
    steps = plan_for(request)
    store.save_plan(request.reference, steps)
    store.append(
        LogEntry(
            EventKind.PLAN_CREATED,
            request.reference,
            tuple(
                (f"step_{step.position}", step.operation.value) for step in steps
            ),
        )
    )
    _advance(store, request, steps, start=0)


def decide(store: Store, command: DecisionCommand) -> None:
    """Resolve the pending approval of a request."""
    tracked = store.tracked(command.reference)
    approval = store.pending_approval(command.reference)
    if approval is None:
        raise ServiceRequestError(f"{command.reference} has nothing pending")
    approval.ensure_role(command.role)

    request = tracked.request
    steps = plan_for(request)
    step = steps[approval.position]
    store.append(
        LogEntry(
            EventKind.APPROVAL_RESOLVED,
            request.reference,
            (
                ("step", step.operation.value),
                ("role", command.role.value),
                ("decision", command.decision.value),
            ),
        )
    )

    if command.decision is Decision.REJECT:
        store.settle_approval(approval, ApprovalState.REJECTED)
        store.set_step_state(request.reference, step.position, StepState.SKIPPED)
        _finish(store, request.reference, RequestState.REJECTED)
        return

    store.settle_approval(approval, ApprovalState.APPROVED)
    if _run_step(store, request, step):
        _advance(store, request, steps, start=step.position + 1)


def _advance(
    store: Store, request: ServiceRequest, steps: tuple[Step, ...], start: int
) -> None:
    for step in steps[start:]:
        outcome = decide_policy(PolicyQuery(step.operation, step.args))
        store.append(
            LogEntry(
                EventKind.POLICY_DECIDED,
                request.reference,
                (
                    ("step", step.operation.value),
                    ("rule", outcome.rule),
                    (
                        "required_role",
                        outcome.required_role.value
                        if outcome.required_role is not None
                        else "none",
                    ),
                ),
            )
        )
        if outcome.required_role is not None:
            store.add_approval(request.reference, step.position, outcome.required_role)
            store.set_step_state(
                request.reference, step.position, StepState.AWAITING_APPROVAL
            )
            store.set_request_state(request.reference, RequestState.AWAITING_APPROVAL)
            store.append(
                LogEntry(
                    EventKind.APPROVAL_REQUESTED,
                    request.reference,
                    (
                        ("step", step.operation.value),
                        ("role", outcome.required_role.value),
                    ),
                )
            )
            return
        if not _run_step(store, request, step):
            return
    _finish(store, request.reference, RequestState.COMPLETED)


def _run_step(store: Store, request: ServiceRequest, step: Step) -> bool:
    operation: Any = OPERATIONS[step.operation]
    account = store.account(request.account)
    try:
        result = operation.run(account, step.args)
    except OperationFailure as failure:
        store.set_step_state(request.reference, step.position, StepState.FAILED)
        store.append(
            LogEntry(
                EventKind.OPERATION_FAILED,
                request.reference,
                (("step", step.operation.value), ("reason", str(failure))),
            )
        )
        _finish(store, request.reference, RequestState.FAILED)
        return False
    _persist(store, account, result.account)
    store.set_step_state(request.reference, step.position, StepState.DONE)
    store.append(
        LogEntry(
            EventKind.OPERATION_RAN,
            request.reference,
            (("step", step.operation.value), ("detail", result.detail)),
        )
    )
    return True


def _persist(store: Store, before: Account, after: Account) -> None:
    if after != before:
        store.save_account(after)


def _finish(store: Store, reference: str, state: RequestState) -> None:
    store.set_request_state(reference, state)
    store.append(
        LogEntry(EventKind.REQUEST_FINISHED, reference, (("state", state.value),))
    )

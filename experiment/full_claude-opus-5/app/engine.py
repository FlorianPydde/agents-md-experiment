"""The runner: expands plans, applies policy, runs steps, records everything."""

from __future__ import annotations

from .domain import (
    Approval,
    RequestRecord,
    ServiceRequest,
    Step,
    World,
)
from .enums import (
    Decision,
    EventKind,
    RequestState,
    Role,
    StepState,
)
from .errors import AppError, OperationFailure
from .operations import OPERATIONS, OperationArgs
from .plans import PLANS
from .policy import decide as policy_decide
from .store import Store


class Runner:
    """Holds the world and the store, so it is a class."""

    def __init__(self, store: Store, world: World) -> None:
        self._store = store
        self._world = world

    @property
    def world(self) -> World:
        return self._world

    def intake(self, request: ServiceRequest) -> RequestRecord:
        operations = PLANS[request.kind]
        record = RequestRecord(
            request=request,
            state=RequestState.RECEIVED,
            arrival=self._store.next_arrival(),
            steps=tuple(
                Step(i, op, StepState.PENDING) for i, op in enumerate(operations)
            ),
            approvals=(),
        )
        self._store.insert_request(record)
        self._store.append(
            request.reference,
            EventKind.REQUEST_RECEIVED,
            (
                ("kind", str(request.kind)),
                ("account", request.account_id),
                ("amount", request.amount.formatted()),
                ("requester", request.requester.name),
                ("origin", str(request.requester.origin)),
            ),
        )
        self._store.append(
            request.reference,
            EventKind.PLAN_CREATED,
            tuple((f"step {i}", str(op)) for i, op in enumerate(operations)),
        )
        return self._advance(self._store.load_request(request.reference))

    def resolve(self, reference: str, role: Role, decision: Decision) -> RequestRecord:
        record = self._store.load_request(reference)
        approval = record.pending_approval()
        if approval is None:
            raise AppError(f"{reference} has nothing pending to decide")
        approval.ensure_role(role)
        self._store.resolve_approval(approval, decision)
        self._store.append(
            reference,
            EventKind.APPROVAL_RESOLVED,
            (
                ("step", str(approval.step_index)),
                ("operation", str(approval.operation)),
                ("role", str(role)),
                ("decision", str(decision)),
            ),
        )
        if decision is Decision.REJECT:
            self._store.set_step_state(
                reference, approval.step_index, StepState.SKIPPED
            )
            return self._finalise(reference, RequestState.REJECTED)
        self._store.set_step_state(reference, approval.step_index, StepState.PENDING)
        return self._advance(self._store.load_request(reference), approved=True)

    # --- internals ----------------------------------------------------

    def _advance(self, record: RequestRecord, approved: bool = False) -> RequestRecord:
        reference = record.request.reference
        while True:
            record = self._store.load_request(reference)
            index = record.next_index()
            if index is None:
                return self._finalise(reference, RequestState.COMPLETED)
            step = record.steps[index]
            args = _args_for(record.request)
            if approved:
                approved = False
            else:
                verdict = policy_decide(step.operation, args)
                self._store.append(
                    reference,
                    EventKind.POLICY_DECIDED,
                    (
                        ("step", str(step.index)),
                        ("operation", str(step.operation)),
                        ("rule", verdict.rule),
                        (
                            "outcome",
                            "runs alone"
                            if verdict.runs_alone
                            else f"needs {verdict.required_role}",
                        ),
                    ),
                )
                if not verdict.runs_alone:
                    assert verdict.required_role is not None
                    return self._request_approval(record, step, verdict.required_role)
            outcome_state = self._run_step(record, step, args)
            if outcome_state is not None:
                return outcome_state

    def _request_approval(
        self, record: RequestRecord, step: Step, role: Role
    ) -> RequestRecord:
        reference = record.request.reference
        self._store.add_approval(
            Approval(reference, step.index, step.operation, role, None)
        )
        self._store.set_step_state(reference, step.index, StepState.AWAITING_APPROVAL)
        self._store.set_request_state(reference, RequestState.AWAITING_APPROVAL)
        self._store.append(
            reference,
            EventKind.APPROVAL_REQUESTED,
            (
                ("step", str(step.index)),
                ("operation", str(step.operation)),
                ("role", str(role)),
            ),
        )
        return self._store.load_request(reference)

    def _run_step(
        self, record: RequestRecord, step: Step, args: OperationArgs
    ) -> RequestRecord | None:
        reference = record.request.reference
        operation = OPERATIONS[step.operation]
        try:
            account = self._world.get(record.request.account_id)
            outcome = operation.run(account, args)
        except OperationFailure as exc:
            self._store.set_step_state(reference, step.index, StepState.FAILED)
            self._store.append(
                reference,
                EventKind.OPERATION_FAILED,
                (
                    ("step", str(step.index)),
                    ("operation", str(step.operation)),
                    ("reason", str(exc)),
                ),
            )
            return self._finalise(reference, RequestState.FAILED)
        self._world.put(outcome.account)
        self._store.save_account(outcome.account)
        self._store.set_step_state(reference, step.index, StepState.DONE)
        self._store.append(
            reference,
            EventKind.OPERATION_RAN,
            (
                ("step", str(step.index)),
                ("operation", str(step.operation)),
                ("result", outcome.note),
            ),
        )
        return None

    def _finalise(self, reference: str, state: RequestState) -> RequestRecord:
        self._store.set_request_state(reference, state)
        self._store.append(
            reference, EventKind.REQUEST_FINALISED, (("state", str(state)),)
        )
        return self._store.load_request(reference)


def _args_for(request: ServiceRequest) -> OperationArgs:
    """Each step reads the arguments it needs from the request that owns it."""
    return OperationArgs(
        account_id=request.account_id,
        amount=request.amount,
        origin=request.requester.origin,
    )

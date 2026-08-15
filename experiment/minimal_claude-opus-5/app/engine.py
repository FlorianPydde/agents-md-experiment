"""The engine: intake a request, run its plan under policy, resolve approvals."""

from __future__ import annotations

from decimal import Decimal

from .domain import (
    Account,
    ApprovalState,
    Decision,
    DecisionEntry,
    EventKind,
    OperationCall,
    OperationName,
    RequestState,
    Role,
    ServiceRequest,
    StepState,
    plan_for,
)
from .errors import AppError
from .operations import OPERATIONS, OperationFailure, run_operation
from .policy import evaluate
from .store import RequestRecord, Store
from .world import World


class Engine:
    """Drives requests through their plans, recording everything in the store."""

    def __init__(self, store: Store, world: World) -> None:
        self.store = store
        self.world = world

    # -- intake ---------------------------------------------------------

    def intake(self, request: ServiceRequest) -> None:
        record = RequestRecord(
            reference=request.reference,
            seq=self.store.next_seq(),
            kind=request.kind,
            account=request.account,
            amount=request.amount,
            requester_name=request.requester.name,
            requester_role=request.requester.role,
            requester_origin=request.requester.origin,
            state=RequestState.RECEIVED,
        )
        self.store.insert_request(record)
        self.store.append_log(
            request.reference,
            EventKind.REQUEST_RECEIVED,
            (
                ("kind", request.kind.value),
                ("account", request.account),
                ("amount", f"{request.amount:.2f}"),
                ("requester", request.requester.name),
                ("origin", request.requester.origin.value),
            ),
        )
        operations = plan_for(request.kind)
        self.store.insert_steps(request.reference, operations)
        self.store.append_log(
            request.reference,
            EventKind.PLAN_CREATED,
            tuple(
                (f"step_{position}", operation.value)
                for position, operation in enumerate(operations)
            ),
        )
        self._advance(request.reference, from_position=0)

    # -- decisions ------------------------------------------------------

    def decide(self, entry: DecisionEntry) -> None:
        if self.store.find_request(entry.reference) is None:
            raise AppError(f"no such request: {entry.reference}")
        approval = self.store.pending_approval(entry.reference)
        if approval is None:
            raise AppError(f"request '{entry.reference}' has nothing pending approval")
        if approval.required_role is not entry.role:
            raise AppError(
                f"request '{entry.reference}' requires role"
                f" '{approval.required_role.value}', not '{entry.role.value}'"
            )
        approved = entry.decision is Decision.APPROVE
        self.store.resolve_approval(
            approval.id,
            entry.role,
            entry.decision,
            ApprovalState.APPROVED if approved else ApprovalState.REJECTED,
        )
        self.store.append_log(
            entry.reference,
            EventKind.APPROVAL_RESOLVED,
            (
                ("step", str(approval.position)),
                ("operation", approval.operation.value),
                ("role", entry.role.value),
                ("decision", entry.decision.value),
            ),
        )
        if not approved:
            self.store.set_step_state(entry.reference, approval.position, StepState.REJECTED)
            self._finalise(entry.reference, RequestState.REJECTED)
            return
        if self._run_step(entry.reference, approval.position, approval.operation):
            self._advance(entry.reference, from_position=approval.position + 1)

    # -- internals ------------------------------------------------------

    def _call_for(self, reference: str, operation: OperationName) -> OperationCall:
        record = self.store.find_request(reference)
        if record is None:
            raise AppError(f"no such request: {reference}")
        return OperationCall(
            operation=operation,
            account=record.account,
            amount=record.amount,
            origin=record.requester_origin,
        )

    def _advance(self, reference: str, *, from_position: int) -> None:
        steps = self.store.steps_of(reference)
        for step in steps[from_position:]:
            call = self._call_for(reference, step.operation)
            decision = evaluate(call)
            required = decision.required_role
            self.store.append_log(
                reference,
                EventKind.POLICY_DECIDED,
                (
                    ("step", str(step.position)),
                    ("operation", step.operation.value),
                    ("rule", str(decision.rule.number)),
                    ("reason", decision.rule.description),
                    ("outcome", "needs_approval" if required else "autonomous"),
                    ("required_role", required.value if required else "none"),
                ),
            )
            if required is not None:
                self._request_approval(reference, step.position, step.operation, required)
                return
            if not self._run_step(reference, step.position, step.operation):
                return
        self._finalise(reference, RequestState.COMPLETED)

    def _request_approval(
        self, reference: str, position: int, operation: OperationName, role: Role
    ) -> None:
        self.store.insert_approval(reference, position, operation, role)
        self.store.set_step_state(reference, position, StepState.AWAITING_APPROVAL)
        self.store.set_request_state(reference, RequestState.AWAITING_APPROVAL)
        self.store.append_log(
            reference,
            EventKind.APPROVAL_REQUESTED,
            (
                ("step", str(position)),
                ("operation", operation.value),
                ("required_role", role.value),
            ),
        )

    def _run_step(self, reference: str, position: int, operation: OperationName) -> bool:
        """Run one step. Returns True when the request may continue."""
        call = self._call_for(reference, operation)
        try:
            outcome = run_operation(call, self.world)
        except OperationFailure as failure:
            self.store.set_step_state(reference, position, StepState.FAILED)
            self.store.append_log(
                reference,
                EventKind.OPERATION_FAILED,
                (
                    ("step", str(position)),
                    ("operation", operation.value),
                    ("reason", str(failure)),
                ),
            )
            self._finalise(reference, RequestState.FAILED)
            return False
        self.store.set_step_state(reference, position, StepState.DONE)
        self.store.append_log(
            reference,
            EventKind.OPERATION_RAN,
            (
                ("step", str(position)),
                ("operation", operation.value),
                ("materiality", OPERATIONS[operation].materiality.value),
                *outcome.detail,
            ),
        )
        self.store.save_accounts(self.world.sorted_accounts())
        return True

    def _finalise(self, reference: str, state: RequestState) -> None:
        self.store.set_request_state(reference, state)
        self.store.append_log(
            reference, EventKind.REQUEST_FINALISED, (("state", state.value),)
        )
        self.store.save_accounts(self.world.sorted_accounts())


def format_request_line(reference: str, kind: str, state: str) -> str:
    return f"{reference:<10}{kind:<20}{state}"


def format_account_line(account: Account) -> str:
    balance: Decimal = account.balance
    mark = "frozen" if account.frozen else "active"
    return f"{account.id:<10}{balance:>10.2f}  {mark}"
